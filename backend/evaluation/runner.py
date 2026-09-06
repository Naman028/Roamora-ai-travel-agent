from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from backend.app.models.chat_models import ChatRequest
from backend.app.services.generation_service import run_travel_generation
from backend.evaluation import enforce_evaluation_offline
from backend.evaluation.contracts import (
    ApplicationStateFixture,
    ArtifactIdentity,
    EvaluationCase,
    EvaluationLane,
    EvaluationRunReport,
    EvaluationSuite,
    ModelVariant,
    ProviderFixture,
    ProviderName,
    RunStatus,
    TimingMetrics,
    TurnResult,
)
from backend.evaluation.fixtures import (
    EvaluationNetworkAccessError,
    blocked_external_network,
    build_model_lane_context,
    controlled_application_clock,
    controlled_provider_services,
)
from backend.evaluation.identity import (
    capture_artifact_identity,
    protected_artifact_hashes,
)
from backend.evaluation.loading import (
    load_application_states,
    load_provider_fixtures,
    load_suite,
    validate_sealed_inputs,
)
from backend.evaluation.reporting import (
    aggregate_results,
    environment_identity,
    write_report,
)
from backend.evaluation.scoring import score_turn
from backend.fine_tuning.scripts.adapter_loader import AdapterConfig


SAFE_EVALUATION_RUN_ID = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z(?:-rescored)?-[0-9a-f]{8}$"
)


class EvaluationOutputPathError(RuntimeError):
    """Raised when an evaluator output path is not a safe child directory."""


def _resolve_output_directory(output_root: Path, run_id: str) -> Path:
    if SAFE_EVALUATION_RUN_ID.fullmatch(run_id) is None:
        raise EvaluationOutputPathError("evaluation_output_run_id_invalid")
    try:
        resolved_root = output_root.resolve()
        resolved_candidate = (resolved_root / run_id).resolve()
    except (OSError, RuntimeError) as exc:
        raise EvaluationOutputPathError("evaluation_output_path_unresolvable") from exc
    try:
        relative_candidate = resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise EvaluationOutputPathError("evaluation_output_path_outside_root") from exc
    if not relative_candidate.parts:
        raise EvaluationOutputPathError("evaluation_output_path_is_root")
    return resolved_candidate


def _new_run_id(started_utc: datetime, *, rescore: bool = False) -> str:
    kind = "-rescored" if rescore else ""
    return f"{started_utc.strftime('%Y%m%dT%H%M%SZ')}{kind}-{uuid.uuid4().hex[:8]}"


@dataclass
class _GenerationTrace:
    raw_outputs: list[str] = field(default_factory=list)
    prompt_hashes: list[str] = field(default_factory=list)
    generation_seconds: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0
    loaded_variants: list[ModelVariant] = field(default_factory=list)
    model_load_seconds: float = 0.0
    generation_max_new_tokens: list[int] = field(default_factory=list)
    model_cache_hits: int = 0


@dataclass
class _ApplicationTrace:
    enrichment_provider_call_counts: dict[ProviderName, int] = field(
        default_factory=dict
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _count_prompt_tokens(tokenizer, prompt: str) -> int:
    messages = [{"role": "user", "content": prompt}]
    if getattr(tokenizer, "chat_template", None):
        prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    encoded = tokenizer(prompt, return_tensors="pt", truncation=False)
    return int(encoded["input_ids"].shape[-1])


def _count_output_tokens(tokenizer, output: str) -> int:
    encoded = tokenizer(output, return_tensors="pt", add_special_tokens=False)
    return int(encoded["input_ids"].shape[-1])


def _cuda_peak_vram() -> int | None:
    try:
        import torch

        if torch.cuda.is_available():
            return int(torch.cuda.max_memory_allocated())
    except ImportError:
        pass
    return None


def _reset_cuda_peak_vram() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except ImportError:
        pass


def initialize_cuda() -> float | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        started = time.perf_counter()
        torch.empty(1, device="cuda")
        torch.cuda.synchronize()
        return time.perf_counter() - started
    except ImportError:
        return None


def _write_raw_outputs(
    raw_root: Path,
    case_id: str,
    turn_index: int,
    lane: EvaluationLane,
    variant: ModelVariant,
    outputs: list[str],
) -> list[str]:
    paths: list[str] = []
    for attempt, output in enumerate(outputs, start=1):
        path = (
            raw_root
            / variant.value
            / lane.value
            / f"{case_id}-turn-{turn_index}-attempt-{attempt}.txt"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
        paths.append(path.relative_to(raw_root.parent).as_posix())
    return paths


def _read_source_raw_outputs(source_dir: Path, paths: list[str]) -> list[str]:
    source_root = source_dir.resolve()
    outputs: list[str] = []
    for relative_path in paths:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise RuntimeError("source_report_raw_output_path_must_be_relative")
        resolved = (source_root / candidate).resolve()
        if not resolved.is_relative_to(source_root):
            raise RuntimeError("source_report_raw_output_path_outside_run")
        if not resolved.is_file():
            raise RuntimeError("source_report_raw_output_missing")
        outputs.append(resolved.read_text(encoding="utf-8"))
    return outputs


@contextmanager
def _trace_application_generation() -> Iterator[_GenerationTrace]:
    from backend.app.services import ai_model_service as model_service

    trace = _GenerationTrace()
    original_generate = model_service.generate_text
    original_get_model = model_service._get_model

    def traced_get_model(variant, config):
        normalized_variant = ModelVariant(variant)
        trace.loaded_variants.append(normalized_variant)
        loaded = model_service._loaded_model
        if loaded is not None and loaded.variant == variant:
            trace.model_cache_hits += 1
        started = time.perf_counter()
        result = original_get_model(variant, config)
        trace.model_load_seconds += time.perf_counter() - started
        return result

    def traced_generate(tokenizer, model, prompt, config):
        trace.attempts += 1
        trace.generation_max_new_tokens.append(config.model_max_new_tokens)
        trace.prompt_hashes.append(_sha256_text(prompt))
        trace.prompt_tokens += _count_prompt_tokens(tokenizer, prompt)
        started = time.perf_counter()
        output = original_generate(tokenizer, model, prompt, config)
        trace.generation_seconds += time.perf_counter() - started
        trace.raw_outputs.append(output)
        trace.output_tokens += _count_output_tokens(tokenizer, output)
        return output

    model_service._get_model = traced_get_model
    model_service.generate_text = traced_generate
    try:
        yield trace
    finally:
        model_service._get_model = original_get_model
        model_service.generate_text = original_generate


@contextmanager
def _trace_application_enrichment(
    provider_counts: dict[ProviderName, int],
) -> Iterator[_ApplicationTrace]:
    from backend.app.services.api_context_service import api_context_service

    trace = _ApplicationTrace()
    original_build = api_context_service.build_api_context_from_message

    async def traced_build(message, existing_context):
        try:
            return await original_build(message, existing_context)
        finally:
            trace.enrichment_provider_call_counts = dict(provider_counts)

    api_context_service.build_api_context_from_message = traced_build
    try:
        yield trace
    finally:
        api_context_service.build_api_context_from_message = original_build


def _source_values(value: Any) -> list[str]:
    sources: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "source" and isinstance(nested, str):
                sources.add(nested)
            sources.update(_source_values(nested))
    elif isinstance(value, list):
        for item in value:
            sources.update(_source_values(item))
    return sorted(sources)


def _has_stable_id_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"id", "grounding_id", "provider_id", "poi_id"} and isinstance(nested, str):
                return True
            if _has_stable_id_field(nested):
                return True
    elif isinstance(value, list):
        return any(_has_stable_id_field(item) for item in value)
    return False


def _model_lane_turn(
    *,
    case: EvaluationCase,
    turn_index: int,
    fixture: ProviderFixture,
    variant: ModelVariant,
    raw_root: Path,
    cuda_initialization_seconds: float | None,
) -> TurnResult:
    from backend.app.services.ai_model_service import (
        _build_prompt,
        _get_model,
        detect_user_intent,
        safe_max_new_tokens,
    )
    from backend.fine_tuning.scripts.adapter_loader import generate_text

    turn = case.turns[turn_index - 1]
    context = build_model_lane_context(turn, fixture)
    intent = detect_user_intent(turn.message)
    prompt = _build_prompt(turn.message, context, intent)
    prompt_hash = _sha256_text(prompt)
    raw_outputs: list[str] = []
    loaded_variants: list[ModelVariant] = []
    model_load_seconds: float | None = None
    generation_seconds: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    generation_attempts = 0
    status = RunStatus.COMPLETED
    error_code: str | None = None
    config = AdapterConfig.from_env()
    config = replace(
        config,
        model_max_new_tokens=safe_max_new_tokens(None, turn.message),
    )
    _reset_cuda_peak_vram()
    with blocked_external_network():
        try:
            loaded_variants.append(variant)
            load_started = time.perf_counter()
            tokenizer, model = _get_model(variant.value, config)
            model_load_seconds = time.perf_counter() - load_started
            prompt_tokens = _count_prompt_tokens(tokenizer, prompt)
            generation_started = time.perf_counter()
            generation_attempts += 1
            output = generate_text(tokenizer, model, prompt, config)
            generation_seconds = time.perf_counter() - generation_started
            raw_outputs.append(output)
            output_tokens = _count_output_tokens(tokenizer, output)
        except EvaluationNetworkAccessError:
            raise
        except Exception as exc:
            status = RunStatus.EXECUTION_FAILED
            error_code = type(exc).__name__

    parse_started = time.perf_counter()
    scores, failures = score_turn(
        case=case,
        turn=turn,
        lane=EvaluationLane.MODEL,
        requested_model=variant,
        raw_outputs=raw_outputs,
        public_response=None,
        fixture=fixture,
        provider_call_counts={name: 0 for name in ProviderName},
        loaded_variants=loaded_variants,
    )
    parse_seconds = time.perf_counter() - parse_started
    raw_files = _write_raw_outputs(
        raw_root,
        case.case_id,
        turn_index,
        EvaluationLane.MODEL,
        variant,
        raw_outputs,
    )
    return TurnResult(
        case_id=case.case_id,
        turn_index=turn_index,
        lane=EvaluationLane.MODEL,
        model_variant=variant,
        status=status,
        prompt_sha256=prompt_hash,
        raw_output_files=raw_files,
        generation_attempts=generation_attempts,
        loaded_variants=loaded_variants,
        provider_call_counts={name: 0 for name in ProviderName},
        scores=scores,
        hard_failures=failures,
        timings=TimingMetrics(
            cuda_initialization_seconds=cuda_initialization_seconds,
            model_load_seconds=model_load_seconds,
            adapter_attachment_seconds=None,
            generation_seconds=generation_seconds,
            parse_normalization_seconds=parse_seconds,
            ttft_seconds=None,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            tokens_per_second=(
                output_tokens / generation_seconds
                if output_tokens is not None and generation_seconds
                else None
            ),
            peak_vram_bytes=_cuda_peak_vram(),
        ),
        observations={
            "prompt_contract": (
                "compact_itinerary_two_root_keys"
                if intent == "itinerary_generation"
                else "legacy_dashboard_prompt"
            ),
            "application_retry_available": False,
            "provider_network_used": False,
            "effective_max_new_tokens": config.model_max_new_tokens,
        },
        sanitized_error_code=error_code,
    )


async def _application_lane_turn(
    *,
    case: EvaluationCase,
    turn_index: int,
    fixture: ProviderFixture,
    state: ApplicationStateFixture,
    variant: ModelVariant,
    raw_root: Path,
    cuda_initialization_seconds: float | None,
) -> TurnResult:
    turn = case.turns[turn_index - 1]
    request = ChatRequest(
        session_id=f"{state.session_id_prefix}-{case.case_id}",
        message=turn.message,
        model_variant=variant.value,
        max_new_tokens=state.max_new_tokens,
    )
    response_dict: dict[str, Any] | None = None
    status = RunStatus.COMPLETED
    error_code: str | None = None
    _reset_cuda_peak_vram()
    started = time.perf_counter()
    with (
        blocked_external_network(),
        controlled_provider_services(fixture) as provider_recorder,
        controlled_application_clock(state.fixed_utc),
        _trace_application_generation() as trace,
        _trace_application_enrichment(provider_recorder.counts) as app_trace,
    ):
        try:
            response = await run_travel_generation(
                request,
                request_id=f"eval-{case.case_id}-{turn_index}",
            )
            response_dict = response.model_dump(mode="json")
        except EvaluationNetworkAccessError:
            raise
        except Exception as exc:
            status = RunStatus.EXECUTION_FAILED
            error_code = type(exc).__name__
    application_seconds = time.perf_counter() - started
    scores, failures = score_turn(
        case=case,
        turn=turn,
        lane=EvaluationLane.APPLICATION,
        requested_model=variant,
        raw_outputs=trace.raw_outputs,
        public_response=response_dict,
        fixture=fixture,
        provider_call_counts=provider_recorder.counts,
        loaded_variants=trace.loaded_variants,
    )
    raw_files = _write_raw_outputs(
        raw_root,
        case.case_id,
        turn_index,
        EvaluationLane.APPLICATION,
        variant,
        trace.raw_outputs,
    )
    output_tokens = trace.output_tokens or None
    generation_seconds = trace.generation_seconds or None
    return TurnResult(
        case_id=case.case_id,
        turn_index=turn_index,
        lane=EvaluationLane.APPLICATION,
        model_variant=variant,
        status=status,
        prompt_sha256=trace.prompt_hashes[0] if trace.prompt_hashes else None,
        raw_output_files=raw_files,
        generation_attempts=trace.attempts,
        loaded_variants=trace.loaded_variants,
        provider_call_counts=provider_recorder.counts,
        retry_used=trace.attempts > 1,
        fallback_used=response_dict.get("fallback_used") if response_dict else None,
        selected_model=(
            ModelVariant(response_dict["selected_model"])
            if response_dict and response_dict.get("selected_model")
            else None
        ),
        adapter_loaded=response_dict.get("adapter_loaded") if response_dict else None,
        scores=scores,
        hard_failures=failures,
        timings=TimingMetrics(
            cuda_initialization_seconds=cuda_initialization_seconds,
            model_load_seconds=trace.model_load_seconds or None,
            adapter_attachment_seconds=None,
            generation_seconds=generation_seconds,
            parse_normalization_seconds=None,
            application_total_seconds=application_seconds,
            ttft_seconds=None,
            prompt_tokens=trace.prompt_tokens or None,
            output_tokens=output_tokens,
            tokens_per_second=(
                output_tokens / generation_seconds
                if output_tokens is not None and generation_seconds
                else None
            ),
            peak_vram_bytes=_cuda_peak_vram(),
        ),
        public_response=response_dict,
        observations={
            "enrichment_provider_call_counts": {
                name.value: app_trace.enrichment_provider_call_counts.get(name, 0)
                for name in ProviderName
            },
            "static_flight_fallback_reached": bool(
                response_dict
                and response_dict.get("dashboard_payload", {})
                .get("flights", {})
                .get("source") == "static_fallback"
            ),
            "static_hotel_fallback_reached": bool(
                response_dict
                and response_dict.get("dashboard_payload", {})
                .get("hotels", {})
                .get("source") == "static_fallback"
            ),
            "adapter_loaded_without_model_generation": bool(
                response_dict
                and response_dict.get("adapter_loaded")
                and trace.attempts == 0
            ),
            "no_budget_input": turn.travel_info.budget is None,
            "reported_budget": (
                response_dict.get("dashboard_payload", {})
                .get("trip_summary", {})
                .get("budget")
                if response_dict
                else None
            ),
            "reported_sources": _source_values(response_dict),
            "stable_grounding_id_fields_in_public_response": _has_stable_id_field(
                response_dict
            ),
            "effective_max_new_tokens": trace.generation_max_new_tokens,
            "reported_retry_used": (
                response_dict.get("retry_used") if response_dict else None
            ),
            "model_cache_hits": trace.model_cache_hits,
            "provider_network_used": False,
        },
        sanitized_error_code=error_code,
    )


async def execute_evaluation(
    *,
    split: str,
    variants: list[ModelVariant],
    lanes: list[EvaluationLane],
    output_root: Path,
    case_limit: int | None = None,
) -> tuple[EvaluationRunReport, Path, Path]:
    enforce_evaluation_offline()
    seal = validate_sealed_inputs(split)
    suite = load_suite(split)
    fixtures = load_provider_fixtures()
    states = load_application_states()
    if len(states.fixtures) != 1:
        raise RuntimeError("evaluation_requires_one_canonical_application_state")
    state = states.fixtures[0]
    identity = capture_artifact_identity(seal.combined_sha256)
    protected_before = protected_artifact_hashes(identity)
    cuda_initialization_seconds = initialize_cuda()
    started_utc = datetime.now(UTC)
    run_id = _new_run_id(started_utc)
    output_dir = _resolve_output_directory(output_root, run_id)
    raw_root = output_dir / "raw"
    cases = suite.cases[:case_limit] if case_limit is not None else suite.cases
    evaluated_suite = suite.model_copy(update={"cases": cases})
    results: list[TurnResult] = []

    for variant in variants:
        for lane in lanes:
            for case in cases:
                for turn_index, turn in enumerate(case.turns, start=1):
                    fixture = fixtures[turn.provider_fixture_id]
                    if lane == EvaluationLane.MODEL:
                        result = _model_lane_turn(
                            case=case,
                            turn_index=turn_index,
                            fixture=fixture,
                            variant=variant,
                            raw_root=raw_root,
                            cuda_initialization_seconds=cuda_initialization_seconds,
                        )
                    else:
                        result = await _application_lane_turn(
                            case=case,
                            turn_index=turn_index,
                            fixture=fixture,
                            state=state,
                            variant=variant,
                            raw_root=raw_root,
                            cuda_initialization_seconds=cuda_initialization_seconds,
                        )
                    results.append(result)

    identity_after = capture_artifact_identity(seal.combined_sha256)
    if protected_before != protected_artifact_hashes(identity_after):
        raise RuntimeError("protected_model_artifact_changed_during_evaluation")

    report = EvaluationRunReport(
        schema_version="roamora_evaluation_report_v1",
        run_id=run_id,
        started_utc=started_utc,
        completed_utc=datetime.now(UTC),
        suite_version=suite.suite_version,
        suite_split=suite.split,
        suite_sha256=seal.combined_sha256,
        artifact_identity=identity,
        environment=environment_identity(),
        results=results,
        aggregate=aggregate_results(evaluated_suite, fixtures, results),
        limitations=[
            "Non-English linguistic quality requires fluent human review; automated markers do not establish language support.",
            "The current non-streaming generation function cannot measure true time to first token; TTFT is null.",
            "The current loader exposes combined fine-tuned model load time, not adapter attachment time separately.",
            "Lane B parse and normalization stages are not separately timed because Stage 1 does not modify application code.",
            "Provider fixtures simulate controlled outcomes and never establish current real-provider quality or availability.",
        ],
    )
    json_path, markdown_path = write_report(output_dir, report)
    return report, json_path, markdown_path


def rescore_evaluation(
    *,
    source_report_path: Path,
    output_root: Path,
) -> tuple[EvaluationRunReport, Path, Path]:
    """Re-score persisted evidence without performing model generation."""
    enforce_evaluation_offline()
    source_report_path = source_report_path.resolve()
    source_report = EvaluationRunReport.model_validate_json(
        source_report_path.read_text(encoding="utf-8")
    )
    seal = validate_sealed_inputs(source_report.suite_split.value)
    if source_report.suite_sha256 != seal.combined_sha256:
        raise RuntimeError("source_report_suite_hash_mismatch")
    if source_report.artifact_identity is None:
        raise RuntimeError("source_report_missing_artifact_identity")

    identity = capture_artifact_identity(seal.combined_sha256)
    if protected_artifact_hashes(source_report.artifact_identity) != protected_artifact_hashes(identity):
        raise RuntimeError("source_report_model_artifact_mismatch")

    suite = load_suite(source_report.suite_split.value)
    fixtures = load_provider_fixtures()
    cases = {case.case_id: case for case in suite.cases}
    started_utc = datetime.now(UTC)
    run_id = _new_run_id(started_utc, rescore=True)
    output_dir = _resolve_output_directory(output_root, run_id)
    raw_root = output_dir / "raw"
    rescored_results: list[TurnResult] = []
    source_dir = source_report_path.parent
    for previous in source_report.results:
        case = cases[previous.case_id]
        turn = case.turns[previous.turn_index - 1]
        fixture = fixtures[turn.provider_fixture_id]
        raw_outputs = _read_source_raw_outputs(
            source_dir,
            previous.raw_output_files,
        )
        public_response = (
            previous.public_response
            if isinstance(previous.public_response, dict)
            else None
        )
        scores, failures = score_turn(
            case=case,
            turn=turn,
            lane=previous.lane,
            requested_model=previous.model_variant,
            raw_outputs=raw_outputs,
            public_response=public_response,
            fixture=fixture,
            provider_call_counts=previous.provider_call_counts,
            loaded_variants=previous.loaded_variants,
        )
        raw_files = _write_raw_outputs(
            raw_root,
            previous.case_id,
            previous.turn_index,
            previous.lane,
            previous.model_variant,
            raw_outputs,
        )
        rescored_results.append(
            previous.model_copy(
                update={
                    "scores": scores,
                    "hard_failures": failures,
                    "raw_output_files": raw_files,
                }
            )
        )

    report = EvaluationRunReport(
        schema_version="roamora_evaluation_report_v1",
        run_id=run_id,
        started_utc=started_utc,
        completed_utc=datetime.now(UTC),
        suite_version=source_report.suite_version,
        suite_split=source_report.suite_split,
        suite_sha256=source_report.suite_sha256,
        artifact_identity=identity,
        environment=environment_identity(),
        results=rescored_results,
        aggregate=aggregate_results(suite, fixtures, rescored_results),
        limitations=[
            *source_report.limitations,
            "Scores and aggregates were recomputed from persisted evidence without model regeneration.",
        ],
        source_run_id=source_report.run_id,
        source_evaluation_code_version=(
            source_report.artifact_identity.evaluation_code_version
        ),
    )
    json_path, markdown_path = write_report(output_dir, report)
    return report, json_path, markdown_path
