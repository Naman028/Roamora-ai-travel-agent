from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.observability import (
    bind_request_id,
    log_operation_failure,
    reset_request_id,
)
from backend.app.core.runtime_health import (
    configured_preload_variant,
    runtime_readiness,
)
from backend.app.main import app


@pytest.fixture(autouse=True)
def reset_runtime_environment(monkeypatch):
    monkeypatch.setenv("BACKEND_ENV", "development")
    monkeypatch.setenv("BACKEND_PRELOAD_MODEL", "none")
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)
    monkeypatch.delenv("WANDERLY_API_KEY", raising=False)
    app.state.lifecycle_ready = False
    yield
    app.state.lifecycle_ready = False


def test_failure_log_keeps_safe_metadata_and_omits_exception_text(caplog):
    token = bind_request_id("request-safe-123")
    try:
        with caplog.at_level(logging.ERROR):
            log_operation_failure(
                logging.getLogger("runtime-quality-test"),
                operation="provider_call",
                error=RuntimeError("provider-secret-value"),
            )
    finally:
        reset_request_id(token)

    assert "operation=provider_call" in caplog.text
    assert "request_id=request-safe-123" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "provider-secret-value" not in caplog.text


def test_liveness_is_stable_and_does_not_require_readiness():
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_tracks_lifecycle_without_invoking_generation(monkeypatch):
    def generation_must_not_run(*_args, **_kwargs):
        raise AssertionError("readiness must not invoke generation")

    monkeypatch.setattr(
        "backend.app.main.preload_model",
        generation_must_not_run,
    )
    client = TestClient(app, raise_server_exceptions=False)

    not_ready = client.get("/ready")
    app.state.lifecycle_ready = True
    ready = client.get("/ready")

    assert not_ready.status_code == 503
    assert not_ready.json() == {"status": "not_ready"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_production_readiness_requires_backend_api_key(monkeypatch):
    monkeypatch.setenv("BACKEND_ENV", "production")

    missing_key = runtime_readiness(lifecycle_ready=True)
    monkeypatch.setenv("BACKEND_API_KEY", "configured-secret")
    configured = runtime_readiness(lifecycle_ready=True)

    assert missing_key.ready is False
    assert missing_key.failed_checks == ("api_key_configuration",)
    assert configured.ready is True


def test_preload_configuration_uses_exact_supported_values(monkeypatch):
    monkeypatch.setenv("BACKEND_PRELOAD_MODEL", "base")
    assert configured_preload_variant() == "base"

    monkeypatch.setenv("BACKEND_PRELOAD_MODEL", "fine_tuned")
    assert configured_preload_variant() == "fine_tuned"

    monkeypatch.setenv("BACKEND_PRELOAD_MODEL", "BASE")
    with pytest.raises(ValueError, match="invalid_backend_preload_model"):
        configured_preload_variant()


def test_openapi_documents_runtime_health_contracts():
    schema = app.openapi()

    assert schema["paths"]["/health"]["get"]["responses"]["200"]
    assert schema["paths"]["/ready"]["get"]["responses"]["503"]
    assert schema["paths"]["/api/health"]["get"]["deprecated"] is True


def test_python_dependency_files_are_exact_and_separated():
    backend_root = Path(__file__).resolve().parents[1]
    runtime_lines = _requirement_lines(backend_root / "requirements.txt")
    ci_lines = _requirement_lines(backend_root / "requirements-ci.txt")
    constraint_lines = _requirement_lines(
        backend_root / "constraints-runtime.txt"
    )

    assert all("==" in line for line in runtime_lines)
    assert all("==" in line for line in ci_lines)
    assert "anyio==4.14.2" in ci_lines
    assert not any(line.startswith("pytest==") for line in runtime_lines)
    assert any(line.startswith("pytest==") for line in ci_lines)
    assert "httpx2==2.12.0" in ci_lines
    assert "torch==2.13.0" in runtime_lines
    assert "transformers==5.10.1" in runtime_lines
    assert "protobuf==6.33.5" in runtime_lines
    assert constraint_lines == ["setuptools==83.0.0"]


def test_platform_launchers_preserve_the_cuda_runtime():
    backend_root = Path(__file__).resolve().parents[1]
    launchers = (
        backend_root / "start_backend_windows.ps1",
        backend_root / "start_backend_wsl.sh",
    )

    for launcher in launchers:
        content = launcher.read_text(encoding="utf-8")
        assert "https://download.pytorch.org/whl/cu126" in content
        assert "torch==2.13.0+cu126" in content
        assert "--no-deps" in content


def test_backend_logging_has_no_known_raw_data_patterns():
    backend_root = Path(__file__).resolve().parents[1]
    sources = list((backend_root / "app").rglob("*.py"))
    sources.append(backend_root / "fine_tuning" / "scripts" / "adapter_loader.py")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    banned_patterns = (
        "logger.exception(",
        "response.text",
        "response.url",
        "request.url",
        "DIRECT WEATHER CITY",
        "FLIGHT EXTRACTED ORIGIN",
        "FLIGHT EXTRACTED DESTINATION",
        "Local adapter   :",
    )
    assert all(pattern not in combined for pattern in banned_patterns)


def _requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and not line.lstrip().startswith(("#", "-"))
    ]
