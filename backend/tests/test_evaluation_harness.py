from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import socket
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.evaluation.contracts import (
    ArtifactIdentity,
    EvaluationLane,
    EvaluationRunReport,
    HardFailureCode,
    ModelVariant,
    ProviderName,
    SealedFile,
    SuiteSplit,
)
from backend.evaluation.identity import (
    ArtifactIdentityError,
    _resolve_adapter_path,
    _resolve_model_snapshot,
    capture_artifact_identity,
)
from backend.evaluation.fixtures import (
    EvaluationNetworkAccessError,
    blocked_external_network,
    build_model_lane_context,
)
from backend.evaluation.loading import (
    EVALUATION_ROOT,
    EvaluationIntegrityError,
    FINAL_SUITE_ENVIRONMENT_VARIABLE,
    FINAL_SUITE_LOGICAL_PATH,
    combined_sha256,
    load_application_states,
    load_final_holdout_metadata,
    load_provider_fixtures,
    load_seal,
    load_suite,
    sha256_file,
    validate_sealed_inputs,
)
from backend.evaluation.reporting import (
    aggregate_results,
    coverage_summary,
    write_report,
)
from backend.evaluation.runner import (
    _ApplicationTrace,
    _application_lane_turn,
    _model_lane_turn,
    _read_source_raw_outputs,
    _resolve_output_directory,
    _trace_application_enrichment,
    EvaluationOutputPathError,
    rescore_evaluation,
)
from backend.evaluation.scoring import parse_strict_json_object, score_turn
from backend.fine_tuning.scripts.adapter_loader import AdapterConfig


@pytest.fixture
def private_suite_test_path():
    path = Path(
        "backend/fine_tuning/outputs/evaluation-test/private-final-fixture.json"
    ).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _test_artifact_identity(suite_sha256: str) -> ArtifactIdentity:
    return ArtifactIdentity(
        base_model_id="local/test-base",
        base_revision="test-revision",
        base_config_sha256="1" * 64,
        tokenizer_file_sha256={"tokenizer.json": "2" * 64},
        chat_template_sha256="3" * 64,
        adapter_source="local/test-adapter",
        adapter_config_sha256="4" * 64,
        adapter_weight_sha256="5" * 64,
        generation_configuration={"temperature": 0.0},
        evaluation_suite_sha256=suite_sha256,
        evaluation_code_version="test-evaluator-version",
    )


@pytest.mark.parametrize("inherited_value", [None, "0", "false", "1"])
def test_evaluation_package_forces_offline_before_optional_hf_imports(
    inherited_value,
):
    script = r"""
import json
import os
import sys

value = sys.argv[1]
for variable in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
    if value == "<absent>":
        os.environ.pop(variable, None)
    else:
        os.environ[variable] = value

import backend.evaluation

print(json.dumps({
    "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
    "transformers_offline": os.environ.get("TRANSFORMERS_OFFLINE"),
    "optional_hf_imported": any(
        name.split(".", 1)[0] in {"huggingface_hub", "transformers", "peft"}
        for name in sys.modules
    ),
}))
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            inherited_value if inherited_value is not None else "<absent>",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "hf_hub_offline": "1",
        "transformers_offline": "1",
        "optional_hf_imported": False,
    }


@pytest.mark.skipif(
    importlib.util.find_spec("transformers") is None,
    reason="isolated ML evaluation environment required",
)
def test_installed_hf_stack_cannot_connect_when_parent_requests_online():
    script = r"""
import json
import os
import socket

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "false"
import backend.evaluation
from huggingface_hub import is_offline_mode
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

network_attempts = []
def blocked(*args, **kwargs):
    network_attempts.append("attempted")
    raise RuntimeError("network attempt")

socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
for loader in (
    AutoConfig.from_pretrained,
    AutoTokenizer.from_pretrained,
    AutoModelForCausalLM.from_pretrained,
):
    try:
        loader("roamora/definitely-missing-offline-artifact")
    except Exception:
        pass

print(json.dumps({
    "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
    "transformers_offline": os.environ.get("TRANSFORMERS_OFFLINE"),
    "hub_reports_offline": is_offline_mode(),
    "network_attempts": network_attempts,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "hf_hub_offline": "1",
        "transformers_offline": "1",
        "hub_reports_offline": True,
        "network_attempts": [],
    }


@pytest.mark.skipif(
    importlib.util.find_spec("huggingface_hub") is None,
    reason="isolated ML evaluation environment required",
)
def test_evaluator_rejects_hub_already_imported_online():
    script = r"""
import json
import os

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "false"
from huggingface_hub import is_offline_mode

assert is_offline_mode() is False
try:
    import backend.evaluation
except RuntimeError as exc:
    result = {"error_type": type(exc).__name__, "error": str(exc)}
else:
    result = {"error_type": None, "error": None}
print(json.dumps(result))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "error_type": "EvaluationOfflineModeError",
        "error": "hugging_face_hub_was_imported_online_before_evaluator",
    }


def test_evaluation_schemas_parse_and_cover_every_supported_language():
    development = load_suite("development")

    assert len(development.cases) == 33
    assert {case.language for case in development.cases} == {
        "en", "de", "fr", "it", "pt", "hi", "es", "th"
    }
    assert all(
        case.linguistic_review_required
        for case in development.cases
        if case.language != "en"
    )


def test_final_holdout_metadata_matches_seal_without_exposing_cases():
    seal = validate_sealed_inputs()
    metadata = load_final_holdout_metadata()
    final_entry = next(
        item for item in seal.files if item.path == FINAL_SUITE_LOGICAL_PATH.as_posix()
    )

    assert metadata.sealed is True
    assert metadata.suite_sha256 == final_entry.sha256
    assert metadata.case_count == 16
    assert metadata.turn_count == 17
    assert not (EVALUATION_ROOT / FINAL_SUITE_LOGICAL_PATH).exists()


def test_final_suite_requires_an_explicit_path_outside_repository(monkeypatch):
    monkeypatch.delenv(FINAL_SUITE_ENVIRONMENT_VARIABLE, raising=False)
    with pytest.raises(
        EvaluationIntegrityError,
        match="final_suite_private_path_not_configured",
    ):
        load_suite("final")

    monkeypatch.setenv(FINAL_SUITE_ENVIRONMENT_VARIABLE, "relative/final.json")
    with pytest.raises(
        EvaluationIntegrityError,
        match="final_suite_private_path_must_be_absolute",
    ):
        load_suite("final")

    monkeypatch.setenv(
        FINAL_SUITE_ENVIRONMENT_VARIABLE,
        str((EVALUATION_ROOT / "missing-final.json").resolve()),
    )
    with pytest.raises(
        EvaluationIntegrityError,
        match="final_suite_private_path_unavailable",
    ):
        load_suite("final")

    monkeypatch.setenv(
        FINAL_SUITE_ENVIRONMENT_VARIABLE,
        str(EVALUATION_ROOT / "suites" / "v1" / "development.json"),
    )
    with pytest.raises(
        EvaluationIntegrityError,
        match="final_suite_must_be_stored_outside_repository",
    ):
        load_suite("final")


def test_private_final_suite_validates_hash_metadata_and_family_isolation(
    monkeypatch,
    private_suite_test_path,
):
    monkeypatch.setattr(
        "backend.evaluation.loading.REPOSITORY_ROOT",
        private_suite_test_path.parent / "simulated-repository",
    )
    development = load_suite("development")
    source_case = development.cases[0]
    final_case = source_case.model_copy(
        update={
            "case_id": "synthetic_private_final",
            "scenario_family": "synthetic_private_final_family",
            "split": SuiteSplit.FINAL,
        }
    )
    final_suite = development.model_copy(
        update={
            "split": SuiteSplit.FINAL,
            "sealed": True,
            "cases": [final_case],
        }
    )
    final_path = private_suite_test_path
    final_path.write_text(
        final_suite.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    final_hash = sha256_file(final_path)
    original_seal = load_seal()
    files = [
        SealedFile(
            path=item.path,
            sha256=(
                final_hash
                if item.path == FINAL_SUITE_LOGICAL_PATH.as_posix()
                else item.sha256
            ),
        )
        for item in original_seal.files
    ]
    test_seal = original_seal.model_copy(
        update={"files": files, "combined_sha256": combined_sha256(files)}
    )
    metadata = load_final_holdout_metadata().model_copy(
        update={
            "suite_sha256": final_hash,
            "case_count": 1,
            "turn_count": 1,
            "authored_utc": final_suite.authored_utc,
        }
    )
    monkeypatch.setattr("backend.evaluation.loading.load_seal", lambda: test_seal)
    monkeypatch.setattr(
        "backend.evaluation.loading.load_final_holdout_metadata",
        lambda: metadata,
    )
    monkeypatch.setenv(FINAL_SUITE_ENVIRONMENT_VARIABLE, str(final_path))

    assert validate_sealed_inputs("final") == test_seal


def test_private_final_suite_hash_mismatch_is_terminal(
    monkeypatch,
    private_suite_test_path,
):
    private_suite_test_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "backend.evaluation.loading.REPOSITORY_ROOT",
        private_suite_test_path.parent / "simulated-repository",
    )
    monkeypatch.setenv(
        FINAL_SUITE_ENVIRONMENT_VARIABLE,
        str(private_suite_test_path),
    )

    with pytest.raises(
        EvaluationIntegrityError,
        match="sealed_evaluation_inputs_do_not_match_manifest",
    ):
        validate_sealed_inputs("final")


def test_seal_validation_rejects_a_hash_mismatch(monkeypatch):
    expected = load_seal()
    changed = expected.model_copy(update={"combined_sha256": "0" * 64})
    monkeypatch.setattr("backend.evaluation.loading.load_seal", lambda: changed)

    with pytest.raises(
        EvaluationIntegrityError,
        match="sealed_evaluation_inputs_do_not_match_manifest",
    ):
        validate_sealed_inputs()


def test_identity_resolution_fails_when_required_artifacts_are_not_local(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub-cache"))

    with pytest.raises(
        ArtifactIdentityError,
        match="base_model_revision_not_available_in_local_cache",
    ):
        _resolve_model_snapshot("roamora/missing-base")

    config = replace(
        AdapterConfig.from_env(),
        local_adapter_path="",
        adapter_repo_id=f"missing/{tmp_path.name}",
        adapter_repo_type="dataset",
        adapter_subfolder="missing-checkpoint",
    )
    with pytest.raises(
        ArtifactIdentityError,
        match="adapter_artifact_not_available_locally",
    ):
        _resolve_adapter_path(config)


def test_identity_resolution_accepts_existing_cache_and_local_adapter(
    monkeypatch,
    tmp_path,
):
    hub_root = tmp_path / "hub"
    revision = "cached-revision"
    snapshot = hub_root / "models--roamora--cached-base" / "snapshots" / revision
    snapshot.mkdir(parents=True)
    ref = hub_root / "models--roamora--cached-base" / "refs" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(revision, encoding="utf-8")
    monkeypatch.setenv("HF_HUB_CACHE", str(hub_root))

    resolved_snapshot, resolved_revision = _resolve_model_snapshot(
        "roamora/cached-base"
    )
    assert resolved_snapshot == snapshot.resolve()
    assert resolved_revision == revision

    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}\n", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"test")
    config = replace(AdapterConfig.from_env(), local_adapter_path=str(adapter))

    assert _resolve_adapter_path(config) == adapter.resolve()


def test_identity_capture_blocks_a_resolver_network_attempt(monkeypatch):
    def network_resolver(_suite_sha256):
        socket.create_connection(("127.0.0.1", 9))

    monkeypatch.setattr(
        "backend.evaluation.identity._capture_local_artifact_identity",
        network_resolver,
    )

    with pytest.raises(
        EvaluationNetworkAccessError,
        match="evaluation_external_network_blocked",
    ):
        capture_artifact_identity("0" * 64)


def test_provider_fixture_loading_and_model_context_are_deterministic():
    fixtures = load_provider_fixtures()
    case = load_suite("development").cases[0]
    turn = case.turns[0]
    fixture = fixtures[turn.provider_fixture_id]

    first = build_model_lane_context(turn, fixture)
    second = build_model_lane_context(turn, fixture)

    assert first == second
    assert first is not second
    assert first["flights"] is not second["flights"]
    assert set(fixture.states) == set(ProviderName)


def test_evaluation_network_guard_fails_closed_and_restores_socket():
    original_create_connection = socket.create_connection

    with blocked_external_network():
        with pytest.raises(
            EvaluationNetworkAccessError,
            match="evaluation_external_network_blocked",
        ):
            socket.create_connection(("127.0.0.1", 9))

    assert socket.create_connection is original_create_connection


def test_model_lane_counts_only_actual_generation_calls(monkeypatch):
    from backend.app.services import ai_model_service
    from backend.fine_tuning.scripts import adapter_loader

    case = load_suite("development").cases[0]
    fixture = load_provider_fixtures()[case.turns[0].provider_fixture_id]
    raw_root = Path("backend/fine_tuning/outputs/evaluation-test/raw")

    monkeypatch.setattr(
        ai_model_service,
        "_get_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("load failed")
        ),
    )
    load_failure = _model_lane_turn(
        case=case,
        turn_index=1,
        fixture=fixture,
        variant=ModelVariant.BASE,
        raw_root=raw_root,
        cuda_initialization_seconds=None,
    )
    assert load_failure.generation_attempts == 0

    class _TokenIds:
        shape = (1, 3)

    class _Tokenizer:
        chat_template = None

        def __call__(self, *_args, **_kwargs):
            return {"input_ids": _TokenIds()}

    monkeypatch.setattr(
        ai_model_service,
        "_get_model",
        lambda *_args, **_kwargs: (_Tokenizer(), object()),
    )
    monkeypatch.setattr(
        adapter_loader,
        "generate_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("generation failed")
        ),
    )
    generation_failure = _model_lane_turn(
        case=case,
        turn_index=1,
        fixture=fixture,
        variant=ModelVariant.BASE,
        raw_root=raw_root,
        cuda_initialization_seconds=None,
    )
    assert generation_failure.generation_attempts == 1


def test_model_lane_blocks_a_loader_network_attempt(monkeypatch, tmp_path):
    from backend.app.services import ai_model_service

    case = load_suite("development").cases[0]
    fixture = load_provider_fixtures()[case.turns[0].provider_fixture_id]

    def network_loader(*_args, **_kwargs):
        socket.create_connection(("127.0.0.1", 9))

    monkeypatch.setattr(ai_model_service, "_get_model", network_loader)

    with pytest.raises(
        EvaluationNetworkAccessError,
        match="evaluation_external_network_blocked",
    ):
        _model_lane_turn(
            case=case,
            turn_index=1,
            fixture=fixture,
            variant=ModelVariant.BASE,
            raw_root=tmp_path / "raw",
            cuda_initialization_seconds=None,
        )


def test_rescore_raw_output_paths_cannot_escape_source_run():
    with pytest.raises(
        RuntimeError,
        match="source_report_raw_output_path_outside_run",
    ):
        _read_source_raw_outputs(
            Path("backend/fine_tuning/outputs/evaluation-test/source-run"),
            ["../outside.txt"],
        )


def test_output_directory_rejects_non_evaluator_run_id(tmp_path):
    with pytest.raises(
        EvaluationOutputPathError,
        match="evaluation_output_run_id_invalid",
    ):
        _resolve_output_directory(tmp_path / "output", "../escape")


@pytest.mark.parametrize(
    "source_run_id",
    [
        "../escape",
        "..\\escape",
        r"C:\outside\escape",
        "/tmp/escape",
        r"C:drive-qualified-escape",
        r"\\server\share\escape",
        "20260905T192537Z-fd2a21e7",
        "x" * 4096,
    ],
)
def test_rescore_source_run_id_is_metadata_only(
    monkeypatch,
    tmp_path,
    source_run_id,
):
    seal = load_seal()
    identity = _test_artifact_identity(seal.combined_sha256)
    now = datetime.now(UTC)
    source_report = EvaluationRunReport(
        schema_version="roamora_evaluation_report_v1",
        run_id=source_run_id,
        started_utc=now,
        completed_utc=now,
        suite_version=seal.suite_version,
        suite_split=SuiteSplit.DEVELOPMENT,
        suite_sha256=seal.combined_sha256,
        artifact_identity=identity,
        environment={"python": "test"},
        results=[],
        aggregate={},
        limitations=[],
    )
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_path = source_dir / "report.json"
    source_path.write_text(source_report.model_dump_json(), encoding="utf-8")
    output_root = tmp_path / "output"

    monkeypatch.setattr(
        "backend.evaluation.runner.validate_sealed_inputs",
        lambda _split: seal,
    )
    monkeypatch.setattr(
        "backend.evaluation.runner.capture_artifact_identity",
        lambda _suite_hash: identity,
    )

    report, json_path, markdown_path = rescore_evaluation(
        source_report_path=source_path,
        output_root=output_root,
    )

    assert report.source_run_id == source_run_id
    assert re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-rescored-[0-9a-f]{8}",
        report.run_id,
    )
    assert json_path.parent.parent.resolve() == output_root.resolve()
    assert markdown_path.parent == json_path.parent
    assert json_path.parent.name == report.run_id


def test_enrichment_trace_records_provider_calls_when_enrichment_fails(monkeypatch):
    from backend.app.services.api_context_service import api_context_service

    async def failing_build(_message, _existing_context):
        provider_counts[ProviderName.WEATHER] = 1
        raise RuntimeError("controlled failure")

    provider_counts = {name: 0 for name in ProviderName}
    monkeypatch.setattr(
        api_context_service,
        "build_api_context_from_message",
        failing_build,
    )
    trace: _ApplicationTrace
    with _trace_application_enrichment(provider_counts) as trace:
        with pytest.raises(RuntimeError, match="controlled failure"):
            asyncio.run(
                api_context_service.build_api_context_from_message("message", None)
            )

    assert trace.enrichment_provider_call_counts[ProviderName.WEATHER] == 1


def test_strict_parser_rejects_markdown_wrapping():
    assert parse_strict_json_object('{"itinerary": []}') == {"itinerary": []}
    with pytest.raises(json.JSONDecodeError):
        parse_strict_json_object('```json\n{"itinerary": []}\n```')


def test_scorer_reports_grounding_failures_individually():
    case = next(
        item
        for item in load_suite("development").cases
        if item.case_id == "dev_clean_en_plan"
    )
    turn = case.turns[0]
    fixture = load_provider_fixtures()[turn.provider_fixture_id]
    raw = json.dumps(
        {
            "assistant_message": "A live flight costs EUR 999 and takes 12 hours.",
            "dashboard_payload": {
                "intent": "itinerary_generation",
                "flights": {
                    "status": "available",
                    "source": "live_api",
                    "data": [{
                        "provider_id": "invented-id",
                        "airline": "Invented Air",
                        "flight_number": "ZZ999",
                        "price": "EUR 999",
                    }],
                },
                "hotels": {"status": "available", "source": "live_api", "data": []},
                "map_data": {
                    "markers": [{"lat": 1.23, "lng": 4.56}],
                    "route_segments": [{"duration": "12 hours"}],
                },
            },
        }
    )

    scores, failures = score_turn(
        case=case,
        turn=turn,
        lane=EvaluationLane.MODEL,
        requested_model=ModelVariant.BASE,
        raw_outputs=[raw],
        public_response=None,
        fixture=fixture,
        provider_call_counts={name: 0 for name in ProviderName},
        loaded_variants=[ModelVariant.BASE],
    )

    assert scores.strict_json_valid is True
    assert set(failures) >= {
        HardFailureCode.FABRICATED_FLIGHT,
        HardFailureCode.FABRICATED_PRICE,
        HardFailureCode.FABRICATED_ROUTE,
        HardFailureCode.FABRICATED_COORDINATES,
        HardFailureCode.INVALID_GROUNDING_ID,
    }


def test_scorer_audits_claims_inside_non_strict_fenced_json():
    case = next(
        item
        for item in load_suite("development").cases
        if item.case_id == "dev_clean_en_plan"
    )
    turn = case.turns[0]
    fixture = load_provider_fixtures()[turn.provider_fixture_id]
    raw = """```json
{"itinerary": [{"day": 1, "time": "09:00", "activity": "12 km route", "budget_eur": 999, "price": 999}], "food_recommendations": []}
```"""

    scores, failures = score_turn(
        case=case,
        turn=turn,
        lane=EvaluationLane.MODEL,
        requested_model=ModelVariant.BASE,
        raw_outputs=[raw],
        public_response=None,
        fixture=fixture,
        provider_call_counts={name: 0 for name in ProviderName},
        loaded_variants=[ModelVariant.BASE],
    )

    assert scores.strict_json_valid is False
    assert scores.expected_structure_valid is False
    assert HardFailureCode.FABRICATED_PRICE in failures
    assert HardFailureCode.FABRICATED_ROUTE in failures


def test_missing_application_response_is_not_a_wrong_model_claim():
    case = load_suite("development").cases[0]
    turn = case.turns[0]
    fixture = load_provider_fixtures()[turn.provider_fixture_id]

    scores, failures = score_turn(
        case=case,
        turn=turn,
        lane=EvaluationLane.APPLICATION,
        requested_model=ModelVariant.FINE_TUNED,
        raw_outputs=[],
        public_response=None,
        fixture=fixture,
        provider_call_counts={name: 0 for name in ProviderName},
        loaded_variants=[],
    )

    assert scores.public_schema_valid is None
    assert HardFailureCode.WRONG_SELECTED_MODEL not in failures
    assert HardFailureCode.PUBLIC_SCHEMA_FAILURE not in failures


def test_application_lane_uses_controlled_provider_and_records_current_semantics(
    monkeypatch,
):
    monkeypatch.setenv("BACKEND_ENV", "development")
    monkeypatch.setenv("BACKEND_PRELOAD_MODEL", "none")
    case = next(
        item
        for item in load_suite("development").cases
        if item.case_id == "dev_provider_weather_success"
    )
    turn = case.turns[0]
    fixture = load_provider_fixtures()[turn.provider_fixture_id]
    state = load_application_states().fixtures[0]

    result = asyncio.run(
        _application_lane_turn(
            case=case,
            turn_index=1,
            fixture=fixture,
            state=state,
            variant=ModelVariant.FINE_TUNED,
            raw_root=Path("backend/fine_tuning/outputs/evaluation-test/raw"),
            cuda_initialization_seconds=None,
        )
    )

    assert result.status.value == "completed"
    assert result.generation_attempts == 0
    assert result.provider_call_counts[ProviderName.WEATHER] == 1
    assert result.observations["enrichment_provider_call_counts"]["weather"] == 0
    assert result.observations["adapter_loaded_without_model_generation"] is True
    assert result.selected_model == ModelVariant.FINE_TUNED
    assert result.scores.public_schema_valid is True


def test_report_serialization_is_machine_and_human_readable():
    suite = load_suite("development")
    fixtures = load_provider_fixtures()
    now = datetime.now(UTC)
    report = EvaluationRunReport(
        schema_version="roamora_evaluation_report_v1",
        run_id="test-run",
        started_utc=now,
        completed_utc=now,
        suite_version=suite.suite_version,
        suite_split=suite.split,
        suite_sha256=load_seal().combined_sha256,
        artifact_identity=None,
        environment={"python": "test"},
        results=[],
        aggregate=aggregate_results(suite, fixtures, []),
        limitations=["test limitation"],
    )

    output_path = Path("backend/fine_tuning/outputs/evaluation-test/report")
    json_path, markdown_path = write_report(output_path, report)

    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "test-run"
    assert "# Roamora Base/V1 evaluation report" in markdown_path.read_text(
        encoding="utf-8"
    )
    assert coverage_summary(suite, fixtures)["case_count"] == 33


def test_partial_suite_coverage_uses_only_selected_cases():
    suite = load_suite("development")
    selected = suite.model_copy(update={"cases": suite.cases[:1]})
    coverage = coverage_summary(selected, load_provider_fixtures())

    assert coverage["case_count"] == 1
    assert coverage["turn_count"] == 1
