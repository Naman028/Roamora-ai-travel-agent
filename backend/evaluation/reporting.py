from __future__ import annotations

import json
import math
import os
import platform
import sys
from collections import Counter, defaultdict
from importlib import metadata
from pathlib import Path
from pydantic import JsonValue

from backend.evaluation.contracts import (
    EvaluationRunReport,
    EvaluationSuite,
    ProviderFixture,
    ProviderName,
    TurnResult,
)


EVALUATOR_PACKAGES = (
    "torch",
    "transformers",
    "accelerate",
    "peft",
    "huggingface-hub",
    "safetensors",
    "pydantic",
)


def environment_identity() -> dict[str, JsonValue]:
    packages: dict[str, str | None] = {}
    for package in EVALUATOR_PACKAGES:
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None

    cuda: dict[str, JsonValue] = {"available": False}
    try:
        import torch

        cuda["available"] = torch.cuda.is_available()
        cuda["torch_cuda_version"] = torch.version.cuda
        if torch.cuda.is_available():
            cuda["device_name"] = torch.cuda.get_device_name(0)
            cuda["device_count"] = torch.cuda.device_count()
    except ImportError:
        cuda["reason"] = "torch_not_installed"

    return {
        "python": sys.version.split()[0],
        "python_executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "packages": packages,
        "cuda": cuda,
        "backend_environment": os.getenv("BACKEND_ENV", "development"),
    }


def coverage_summary(
    suite: EvaluationSuite,
    fixtures: dict[str, ProviderFixture],
) -> dict[str, JsonValue]:
    languages = Counter(case.language for case in suite.cases)
    skills = Counter(skill for case in suite.cases for skill in case.skills)
    provider_states: Counter[str] = Counter()
    turn_count = 0
    multi_turn_cases = 0
    for case in suite.cases:
        turn_count += len(case.turns)
        multi_turn_cases += int(len(case.turns) > 1)
        for turn in case.turns:
            fixture = fixtures[turn.provider_fixture_id]
            for provider in ProviderName:
                provider_states[f"{provider.value}:{fixture.states[provider].value}"] += 1
    return {
        "case_count": len(suite.cases),
        "turn_count": turn_count,
        "multi_turn_case_count": multi_turn_cases,
        "languages": dict(sorted(languages.items())),
        "skills": dict(sorted(skills.items())),
        "provider_states": dict(sorted(provider_states.items())),
    }


def aggregate_results(
    suite: EvaluationSuite,
    fixtures: dict[str, ProviderFixture],
    results: list[TurnResult],
) -> dict[str, JsonValue]:
    groups: dict[str, list[TurnResult]] = defaultdict(list)
    for result in results:
        groups[f"{result.model_variant.value}:{result.lane.value}"].append(result)

    by_group: dict[str, JsonValue] = {}
    for key, group in sorted(groups.items()):
        count = len(group)
        failures = Counter(code.value for item in group for code in item.hard_failures)
        retry_count = sum(item.retry_used is True for item in group)
        fallback_count = sum(item.fallback_used is True for item in group)
        schema_results = [
            item.scores.public_schema_valid
            for item in group
            if item.scores.public_schema_valid is not None
        ]
        schema_valid = sum(value is True for value in schema_results)
        strict_json_valid = sum(item.scores.strict_json_valid for item in group)
        completed = sum(item.status.value == "completed" for item in group)
        generation_seconds = [
            item.timings.generation_seconds
            for item in group
            if item.timings.generation_seconds is not None
        ]
        application_seconds = [
            item.timings.application_total_seconds
            for item in group
            if item.timings.application_total_seconds is not None
        ]
        throughput = [
            item.timings.tokens_per_second
            for item in group
            if item.timings.tokens_per_second is not None
        ]
        prompt_tokens = [
            float(item.timings.prompt_tokens)
            for item in group
            if item.timings.prompt_tokens is not None
        ]
        output_tokens = [
            float(item.timings.output_tokens)
            for item in group
            if item.timings.output_tokens is not None
        ]
        peak_vram = [
            float(item.timings.peak_vram_bytes)
            for item in group
            if item.timings.peak_vram_bytes is not None
        ]
        score_names = (
            "expected_structure_valid",
            "intent_match",
            "required_terms_present",
            "forbidden_terms_absent",
            "grounding_ids_valid",
            "selected_ids_match",
            "language_heuristic_match",
            "provider_calls_match",
        )
        score_summary: dict[str, JsonValue] = {}
        for score_name in score_names:
            values = [
                getattr(item.scores, score_name)
                for item in group
                if getattr(item.scores, score_name) is not None
            ]
            passed = sum(value is True for value in values)
            score_summary[score_name] = {
                "applicable_count": len(values),
                "passed_count": passed,
                "rate": passed / len(values) if values else None,
            }
        by_group[key] = {
            "result_count": count,
            "completed_count": completed,
            "strict_json_valid_count": strict_json_valid,
            "strict_json_valid_rate": strict_json_valid / count if count else None,
            "public_schema_valid_count": schema_valid,
            "public_schema_valid_rate": (
                schema_valid / len(schema_results) if schema_results else None
            ),
            "retry_count": retry_count,
            "retry_rate": retry_count / count if count else None,
            "fallback_count": fallback_count,
            "fallback_rate": fallback_count / count if count else None,
            "hard_failures": dict(sorted(failures.items())),
            "execution_errors": dict(
                sorted(
                    Counter(
                        item.sanitized_error_code
                        for item in group
                        if item.sanitized_error_code
                    ).items()
                )
            ),
            "scores": score_summary,
            "generation_seconds": _distribution(generation_seconds),
            "application_total_seconds": _distribution(application_seconds),
            "tokens_per_second": _distribution(throughput),
            "prompt_tokens": _distribution(prompt_tokens),
            "output_tokens": _distribution(output_tokens),
            "peak_vram_bytes": _distribution(peak_vram),
        }
    language_metrics = _language_metrics(suite, results)
    multi_turn_metrics = _multi_turn_metrics(suite, results)
    observations = _baseline_observations(suite, results)
    failure_ownership = _failure_ownership(results)
    return {
        "coverage": coverage_summary(suite, fixtures),
        "by_model_and_lane": by_group,
        "language_metrics": language_metrics,
        "multi_turn_metrics": multi_turn_metrics,
        "baseline_observations": observations,
        "failure_ownership": failure_ownership,
        "linguistic_human_review_pending": sum(
            result.scores.linguistic_review_required for result in results
        ),
    }


def _distribution(values: list[float]) -> dict[str, JsonValue]:
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "p50": None,
            "p95": None,
            "maximum": None,
            "mean": None,
            "total": None,
        }
    ordered = sorted(values)

    def nearest_rank(percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]

    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "p50": nearest_rank(0.50),
        "p95": nearest_rank(0.95),
        "maximum": ordered[-1],
        "mean": sum(ordered) / len(ordered),
        "total": sum(ordered),
    }


def _language_metrics(
    suite: EvaluationSuite,
    results: list[TurnResult],
) -> dict[str, JsonValue]:
    case_languages = {case.case_id: case.language for case in suite.cases}
    grouped: dict[str, list[TurnResult]] = defaultdict(list)
    for result in results:
        language = case_languages[result.case_id]
        grouped[f"{result.model_variant.value}:{result.lane.value}:{language}"].append(result)
    output: dict[str, JsonValue] = {}
    for key, group in sorted(grouped.items()):
        applicable = [
            result.scores.language_heuristic_match
            for result in group
            if result.scores.language_heuristic_match is not None
        ]
        passed = sum(value is True for value in applicable)
        output[key] = {
            "turn_count": len(group),
            "heuristic_applicable_count": len(applicable),
            "heuristic_passed_count": passed,
            "heuristic_rate": passed / len(applicable) if applicable else None,
            "human_review_required_count": sum(
                result.scores.linguistic_review_required for result in group
            ),
        }
    return output


def _multi_turn_metrics(
    suite: EvaluationSuite,
    results: list[TurnResult],
) -> dict[str, JsonValue]:
    multi_turn_ids = {case.case_id for case in suite.cases if len(case.turns) > 1}
    selected = [result for result in results if result.case_id in multi_turn_ids]
    selection_applicable = [
        result.scores.selected_ids_match
        for result in selected
        if result.scores.selected_ids_match is not None
    ]
    return {
        "case_count": len(multi_turn_ids),
        "result_count": len(selected),
        "intent_match_count": sum(result.scores.intent_match for result in selected),
        "selected_id_applicable_count": len(selection_applicable),
        "selected_id_match_count": sum(value is True for value in selection_applicable),
        "public_schema_valid_count": sum(
            result.scores.public_schema_valid is True for result in selected
        ),
    }


def _baseline_observations(
    suite: EvaluationSuite,
    results: list[TurnResult],
) -> dict[str, JsonValue]:
    cases = {case.case_id: case for case in suite.cases}
    application = [result for result in results if result.lane.value == "application"]
    source_counts: Counter[str] = Counter()
    enrichment_mismatches = 0
    no_budget_default_500 = 0
    missing_stable_ids = 0
    for result in application:
        for source in result.observations.get("reported_sources", []):
            if isinstance(source, str):
                source_counts[source] += 1
        case = cases[result.case_id]
        turn = case.turns[result.turn_index - 1]
        enrichment = result.observations.get("enrichment_provider_call_counts", {})
        if isinstance(enrichment, dict):
            actual = {name for name, count in enrichment.items() if count}
            if not actual and result.status.value == "execution_failed":
                actual = {
                    name.value
                    for name, count in result.provider_call_counts.items()
                    if count
                }
            expected = {name.value for name in turn.expectation.expected_provider_calls}
            enrichment_mismatches += int(actual != expected)
        if result.observations.get("no_budget_input") is True:
            response = result.public_response
            budget_breakdown = None
            if isinstance(response, dict):
                dashboard = response.get("dashboard_payload")
                if isinstance(dashboard, dict):
                    budget_breakdown = dashboard.get("budget_breakdown")
            if isinstance(budget_breakdown, dict):
                total = budget_breakdown.get("total_estimated_cost")
                remaining = budget_breakdown.get("remaining_budget")
                if (
                    isinstance(total, (int, float))
                    and isinstance(remaining, (int, float))
                    and abs((total + remaining) - 500) < 0.001
                ):
                    no_budget_default_500 += 1
        fixture_has_ids = bool(turn.expectation.allowed_grounding_ids)
        if (
            fixture_has_ids
            and result.observations.get(
                "stable_grounding_id_fields_in_public_response"
            ) is False
        ):
            missing_stable_ids += 1
    hard_failures = Counter(
        failure.value for result in results for failure in result.hard_failures
    )
    return {
        "automatic_generation_retry_count": sum(
            result.retry_used is True for result in application
        ),
        "application_fallback_count": sum(
            result.fallback_used is True for result in application
        ),
        "static_flight_fallback_count": sum(
            result.observations.get("static_flight_fallback_reached") is True
            for result in application
        ),
        "static_hotel_fallback_count": sum(
            result.observations.get("static_hotel_fallback_reached") is True
            for result in application
        ),
        "adapter_loaded_without_generation_count": sum(
            result.observations.get("adapter_loaded_without_model_generation") is True
            for result in application
        ),
        "no_budget_default_eur_500_count": no_budget_default_500,
        "enrichment_provider_call_mismatch_count": enrichment_mismatches,
        "missing_stable_grounding_id_field_count": missing_stable_ids,
        "reported_source_occurrences": dict(sorted(source_counts.items())),
        "hard_failure_occurrences": dict(sorted(hard_failures.items())),
        "application_execution_failure_count": sum(
            result.status.value == "execution_failed" for result in application
        ),
    }


def _failure_ownership(results: list[TurnResult]) -> dict[str, JsonValue]:
    model_affected: set[tuple[str, int, str]] = set()
    backend_affected: set[tuple[str, int, str]] = set()
    infrastructure_affected: set[tuple[str, int, str]] = set()
    model_signals = 0
    backend_signals = 0
    for result in results:
        identity = (result.case_id, result.turn_index, result.model_variant.value)
        if result.lane.value == "model":
            failed_scores = (
                not result.scores.strict_json_valid,
                not result.scores.expected_structure_valid,
                not result.scores.intent_match,
                not result.scores.required_terms_present,
                not result.scores.forbidden_terms_absent,
                result.scores.grounding_ids_valid is False,
                result.scores.selected_ids_match is False,
                result.scores.language_heuristic_match is False,
            )
            count = sum(failed_scores) + len(result.hard_failures)
            if count:
                model_affected.add(identity)
                model_signals += count
            if result.status.value == "execution_failed":
                infrastructure_affected.add(identity)
        else:
            observations = result.observations
            count = (
                int(result.status.value == "execution_failed")
                + int(result.retry_used is True)
                + int(result.fallback_used is True)
                + int(result.scores.provider_calls_match is False)
                + int(observations.get("static_flight_fallback_reached") is True)
                + int(observations.get("static_hotel_fallback_reached") is True)
                + int(observations.get("adapter_loaded_without_model_generation") is True)
                + len(result.hard_failures)
            )
            if count:
                backend_affected.add(identity)
                backend_signals += count
    return {
        "method": "affected turns and observable failure signals; causal dataset attribution is not inferred",
        "model_owned": {
            "affected_turn_count": len(model_affected),
            "signal_count": model_signals,
        },
        "backend_owned": {
            "affected_turn_count": len(backend_affected),
            "signal_count": backend_signals,
        },
        "provider_owned": {
            "affected_turn_count": 0,
            "signal_count": 0,
            "note": "Provider outcomes are deterministic test inputs, not live-provider defects.",
        },
        "dataset_owned": {
            "affected_turn_count": 0,
            "signal_count": 0,
            "note": "This evaluation cannot causally attribute failures to the training dataset.",
        },
        "infrastructure_owned": {
            "affected_turn_count": len(infrastructure_affected),
            "signal_count": len(infrastructure_affected),
        },
    }


def write_report(output_dir: Path, report: EvaluationRunReport) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _render_markdown(report: EvaluationRunReport) -> str:
    lines = [
        "# Roamora Base/V1 evaluation report",
        "",
        f"- Run: `{report.run_id}`",
        f"- Suite: `{report.suite_version}` / `{report.suite_split.value}`",
        f"- Suite SHA-256: `{report.suite_sha256}`",
        f"- Started: `{report.started_utc.isoformat()}`",
        f"- Completed: `{report.completed_utc.isoformat()}`",
        "",
        "## Results",
        "",
        "| Model/lane | Results | Completed | Raw strict JSON | Raw structure | Public schema | Retries | Fallbacks | Gen p50/p95 s | Mean tok/s | Peak GiB |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    grouped = report.aggregate.get("by_model_and_lane", {})
    if isinstance(grouped, dict):
        for key, value in grouped.items():
            if not isinstance(value, dict):
                continue
            scores = value.get("scores", {})
            structure = scores.get("expected_structure_valid", {})
            generation = value.get("generation_seconds", {})
            throughput = value.get("tokens_per_second", {})
            vram = value.get("peak_vram_bytes", {})
            peak_bytes = vram.get("maximum")
            peak_gib = peak_bytes / (1024 ** 3) if isinstance(peak_bytes, (int, float)) else None
            public_schema_count: int | str = value.get("public_schema_valid_count", 0)
            if value.get("public_schema_valid_rate") is None:
                public_schema_count = "N/A"
            lines.append(
                f"| {key} | {value.get('result_count')} | {value.get('completed_count')} | "
                f"{value.get('strict_json_valid_count')} | {structure.get('passed_count')} | "
                f"{public_schema_count} | {value.get('retry_count')} | "
                f"{value.get('fallback_count')} | {_number(generation.get('p50'))}/{_number(generation.get('p95'))} | "
                f"{_number(throughput.get('mean'))} | {_number(peak_gib)} |"
            )
    lines.extend(["", "## Hard failures", ""])
    if isinstance(grouped, dict):
        for key, value in grouped.items():
            failures = value.get("hard_failures", {}) if isinstance(value, dict) else {}
            lines.append(f"- `{key}`: `{json.dumps(failures, sort_keys=True)}`")
    lines.extend(["", "## Baseline observations", ""])
    observations = report.aggregate.get("baseline_observations", {})
    if isinstance(observations, dict):
        for key, value in observations.items():
            lines.append(f"- `{key}`: `{json.dumps(value, sort_keys=True)}`")
    lines.extend(["", "## Failure ownership", ""])
    ownership = report.aggregate.get("failure_ownership", {})
    if isinstance(ownership, dict):
        for key, value in ownership.items():
            lines.append(f"- `{key}`: `{json.dumps(value, sort_keys=True)}`")
    lines.extend(["", "## Multi-turn", ""])
    lines.append(
        f"`{json.dumps(report.aggregate.get('multi_turn_metrics', {}), sort_keys=True)}`"
    )
    lines.extend(["", "## Language review", ""])
    lines.append(
        "Automated language markers are diagnostic only. Non-English quality remains pending fluent human review."
    )
    lines.append(
        f"Pending result reviews: `{report.aggregate.get('linguistic_human_review_pending', 0)}`."
    )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report.limitations)
    lines.append("")
    return "\n".join(lines)


def _number(value: object) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"
