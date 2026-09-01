from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core.security import require_api_key
from backend.app.models.chat_models import ChatRequest
from backend.app.services.ai_model_service import coerce_model_dashboard_payload
from backend.app.services.api_context_service import ApiContextService
from backend.fine_tuning.scripts.json_guardrail import _normalize_price_range


def test_extract_travel_info_keeps_origin_and_destination_separate():
    service = ApiContextService()

    result = service.extract_travel_info(
        "What is the cheapest way to spend 3 days in Amsterdam coming from Stuttgart?"
    )

    assert result["origin"] == "Stuttgart"
    assert result["destination"] == "Amsterdam"
    assert result["duration_days"] == 3


def test_extract_travel_info_handles_departing_from_phrase():
    service = ApiContextService()

    result = service.extract_travel_info(
        "Build a 5 day itinerary to Singapore departing from Marseille."
    )

    assert result["origin"] == "Marseille"
    assert result["destination"] == "Singapore"
    assert result["duration_days"] == 5


def test_coerce_dashboard_payload_preserves_model_sections_and_adds_map_marker():
    payload = {
        "trip_summary": {"destination": "Amsterdam"},
        "hotel_options": [{"name": "Budget Stay", "price_per_night": "EUR 80"}],
        "food_recommendations": {"items": [{"name": "FEBO", "price_range": "low"}]},
        "dashboard_actions": [{"type": "show_map"}, "show_food"],
        "itinerary": [{"day": 1, "activity": "Walk the canals"}],
    }

    dashboard = coerce_model_dashboard_payload(payload, "itinerary_generation", {})

    assert dashboard["stay_recommendations"] == [{"name": "Budget Stay", "price_per_night": "EUR 80"}]
    assert dashboard["hotels"]["data"] == [{"name": "Budget Stay", "price_per_night": "EUR 80"}]
    assert dashboard["food_recommendations"] == [{"name": "FEBO", "price_range": "low"}]
    assert "show_map" in dashboard["dashboard_actions"]
    assert dashboard["map_data"]["markers"][0]["label"] == "Amsterdam"
    assert dashboard["map_data"]["markers"][0]["lat"] == pytest.approx(52.3676)


def test_price_range_normalizes_labels_and_numeric_ranges():
    cases = {
        "budget-friendly": "low",
        "cheap": "low",
        "mid_range": "low_to_moderate",
        "moderate": "moderate",
        "fine dining": "high",
        "midi-day": "unknown",
        "EUR 5-10": "low",
        "EUR 20-30": "low_to_moderate",
        "EUR 50-70": "high",
    }

    for value, expected in cases.items():
        assert _normalize_price_range(value) == expected, value


def test_chat_request_rejects_oversized_message_and_unsafe_session_id():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="bad session id", message="hello")

    with pytest.raises(ValidationError):
        ChatRequest(session_id="safe-session", message="x" * 4001)


def test_api_key_dependency_is_opt_in(monkeypatch):
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)
    monkeypatch.delenv("WANDERLY_API_KEY", raising=False)

    app = FastAPI()

    @app.post("/secure", dependencies=[Depends(require_api_key)])
    async def secure():
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/secure").status_code == 200


def test_api_key_dependency_rejects_missing_and_wrong_keys(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", "secret-key")
    monkeypatch.delenv("WANDERLY_API_KEY", raising=False)

    app = FastAPI()

    @app.post("/secure", dependencies=[Depends(require_api_key)])
    async def secure():
        return {"ok": True}

    client = TestClient(app)

    assert client.post("/secure").status_code == 401
    assert client.post("/secure", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/secure", headers={"X-API-Key": "secret-key"}).status_code == 200
