from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.core.api_errors import ApiContractException
from backend.app.core.request_controls import generation_request_controller
from backend.app.main import app
from backend.app.models.chat_models import ChatRequest, ChatResponse
from backend.app.routes import chat_router
from backend.app.services.api_context_service import ApiContextService
from backend.app.services import generation_service


@pytest.fixture(autouse=True)
def reset_backend_controls(monkeypatch):
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)
    monkeypatch.delenv("WANDERLY_API_KEY", raising=False)
    monkeypatch.setenv("BACKEND_ENV", "development")
    monkeypatch.setenv("BACKEND_RATE_LIMIT_PER_MINUTE", "100")
    monkeypatch.setenv("BACKEND_MAX_CONCURRENT_GENERATIONS_PER_CLIENT", "2")
    generation_request_controller.reset()
    yield
    generation_request_controller.reset()


def _response(session_id: str = "test-session") -> ChatResponse:
    return ChatResponse(
        session_id=session_id,
        selected_model="fine_tuned",
        adapter_loaded=True,
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message="A safe response.",
        dashboard_payload={
            "schema_version": "travel_dashboard_v1",
            "intent": "itinerary_generation",
        },
    )


def test_chat_success_uses_typed_contract_and_request_id(monkeypatch):
    async def fake_generation(chat_request, *, request_id):
        assert request_id == "request-1234"
        return _response(chat_request.session_id)

    monkeypatch.setattr(chat_router, "run_travel_generation", fake_generation)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/chat",
        headers={"X-Request-ID": "request-1234"},
        json={"session_id": "safe-session", "message": "Plan a short trip"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-1234"
    payload = response.json()
    assert payload["session_id"] == "safe-session"
    assert payload["dashboard_payload"]["intent"] == "itinerary_generation"
    assert "raw_model_output" not in payload


def test_validation_uses_stable_error_envelope():
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/chat",
        json={"message": "hello", "max_new_tokens": 1801},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["retryable"] is False
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert any(
        item["field"] == "body.max_new_tokens"
        for item in error["field_errors"]
    )


def test_raw_model_output_is_rejected_and_filtered():
    client = TestClient(app, raise_server_exceptions=False)
    rejected = client.post(
        "/chat",
        json={"message": "hello", "include_raw_model_output": True},
    )

    assert rejected.status_code == 422
    response = _response()
    dumped = response.model_dump()
    assert "raw_model_output" not in dumped
    assert "first_raw_model_output" not in dumped
    assert "retry_raw_model_output" not in dumped


def test_generation_timeout_has_safe_public_error(monkeypatch):
    async def slow_generation(_request):
        await asyncio.sleep(0.05)
        return _response()

    monkeypatch.setattr(
        generation_service, "generate_travel_response", slow_generation
    )
    monkeypatch.setattr(
        generation_service, "generation_timeout_seconds", lambda: 0.001
    )

    with pytest.raises(ApiContractException) as captured:
        asyncio.run(
            generation_service.run_travel_generation(
                ChatRequest(message="hello"),
                request_id="timeout-request",
            )
        )

    assert captured.value.status_code == 504
    assert captured.value.code == "GENERATION_TIMEOUT"
    assert captured.value.retryable is True


def test_generation_failure_never_exposes_exception_text(monkeypatch, caplog):
    async def failed_generation(_request):
        raise RuntimeError("provider-secret-value")

    monkeypatch.setattr(
        generation_service, "generate_travel_response", failed_generation
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(ApiContractException) as captured:
            asyncio.run(
                generation_service.run_travel_generation(
                    ChatRequest(message="hello"),
                    request_id="failed-request",
                )
            )

    assert captured.value.status_code == 500
    assert captured.value.code == "GENERATION_FAILED"
    assert "provider-secret-value" not in captured.value.public_message
    assert "provider-secret-value" not in caplog.text
    assert "request_id=failed-request" in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_upstream_service_errors_are_redacted_from_dashboard_warnings():
    class FailingWeatherService:
        async def get_current_weather(self, _destination):
            raise RuntimeError("weather-provider-secret")

    class EmptyTravelServices:
        async def search_flights(self, **_kwargs):
            return None

        async def search_hotels(self, *_args):
            return None

        async def search_events(self, *_args):
            return None

    service = ApiContextService()
    service._weather_service = FailingWeatherService()
    empty_services = EmptyTravelServices()
    service._flight_service = empty_services
    service._hotel_service = empty_services
    service._events_service = empty_services

    context = asyncio.run(
        service.build_api_context_from_message(
            "Plan a 2 day trip from Paris to Berlin",
            {},
        )
    )

    warnings = " ".join(context["warnings"])
    assert "weather-provider-secret" not in warnings
    assert "Weather service unavailable." in warnings


def test_rate_and_concurrency_limits_are_bounded(monkeypatch):
    monkeypatch.setenv("BACKEND_RATE_LIMIT_PER_MINUTE", "1")
    generation_request_controller.acquire("client-a")
    generation_request_controller.release("client-a")

    with pytest.raises(ApiContractException) as rate_error:
        generation_request_controller.acquire("client-a")
    assert rate_error.value.code == "RATE_LIMITED"

    generation_request_controller.reset()
    monkeypatch.setenv("BACKEND_RATE_LIMIT_PER_MINUTE", "10")
    monkeypatch.setenv("BACKEND_MAX_CONCURRENT_GENERATIONS_PER_CLIENT", "1")
    generation_request_controller.acquire("client-b")
    with pytest.raises(ApiContractException) as concurrency_error:
        generation_request_controller.acquire("client-b")
    generation_request_controller.release("client-b")
    assert concurrency_error.value.code == "CONCURRENCY_LIMITED"


def test_production_api_key_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("BACKEND_ENV", "production")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "BACKEND_MISCONFIGURED"


def test_invalid_api_key_uses_safe_401_envelope(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", "expected-secret")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/chat",
        headers={"X-API-Key": "wrong-secret"},
        json={"message": "hello"},
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "INVALID_API_KEY"
    assert "expected-secret" not in response.text
    assert "wrong-secret" not in response.text


def test_chat_rate_limit_uses_safe_429_envelope(monkeypatch):
    async def fake_generation(chat_request, *, request_id):
        return _response(chat_request.session_id)

    monkeypatch.setattr(chat_router, "run_travel_generation", fake_generation)
    monkeypatch.setenv("BACKEND_RATE_LIMIT_PER_MINUTE", "1")
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post("/chat", json={"message": "first"})
    limited = client.post("/chat", json={"message": "second"})

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert limited.json()["error"]["retryable"] is True


def test_disconnected_history_is_explicitly_disabled():
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/chat/history/safe-session")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "HISTORY_DISABLED"


def test_openapi_publishes_typed_chat_and_error_contracts():
    schema = app.openapi()
    chat_path = schema["paths"]["/chat"]
    chat_request = schema["components"]["schemas"]["ChatRequest"]
    chat_response = schema["components"]["schemas"]["ChatResponse"]
    dashboard = schema["components"]["schemas"]["DashboardPayload"]

    assert set(chat_path) == {"post"}
    model_variant = chat_request["properties"]["model_variant"]
    assert model_variant["enum"] == ["base", "fine_tuned"]
    assert model_variant["default"] == "fine_tuned"
    assert "model_variant" not in chat_request["required"]
    assert "raw_model_output" not in chat_response["properties"]
    assert dashboard["properties"]["itinerary"]["items"]["$ref"].endswith(
        "/ItineraryItem"
    )
    assert dashboard["properties"]["flights"]["$ref"].endswith(
        "/FlightSection"
    )
    error_ref = chat_path["post"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert error_ref == "#/components/schemas/ApiErrorResponse"
    assert "APIKeyHeader" in schema["components"]["securitySchemes"]
    assert set(
        schema["paths"]["/api/chat/history/{session_id}"]["get"]["responses"]
    ) == {"501", "422"}


def test_direct_service_limits_are_documented_and_validated():
    schema = app.openapi()
    hotel_parameters = {
        item["name"]: item
        for item in schema["paths"]["/api/hotels/search"]["post"][
            "parameters"
        ]
    }
    duration_parameters = {
        item["name"]: item
        for item in schema["paths"]["/api/itinerary/generate"]["post"][
            "parameters"
        ]
    }

    assert hotel_parameters["guests"]["schema"]["minimum"] == 1
    assert hotel_parameters["guests"]["schema"]["maximum"] == 10
    assert duration_parameters["duration_days"]["schema"]["minimum"] == 1
    assert duration_parameters["duration_days"]["schema"]["maximum"] == 7

    client = TestClient(app, raise_server_exceptions=False)
    invalid_guests = client.post(
        "/api/hotels/search",
        params={
            "destination": "Berlin",
            "check_in": "2026-08-01",
            "check_out": "2026-08-02",
            "guests": 0,
        },
    )
    assert invalid_guests.status_code == 422
    assert invalid_guests.json()["error"]["code"] == "VALIDATION_ERROR"
