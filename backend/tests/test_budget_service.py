from __future__ import annotations

import asyncio
from time import perf_counter

import pytest
import httpx

from backend.app.services.ai_model_service import (
    itinerary_has_required_days,
    normalize_itinerary_slots,
    safe_json_loads_from_model,
    safe_max_new_tokens,
)
from backend.app.services.api_context_service import ApiContextService
from backend.app.services.budget_service import calculate_budget_breakdown
from backend.app.services.hotel_service import HotelService
from backend.fine_tuning.scripts.adapter_loader import has_complete_json_object


def _itinerary_payload(**overrides):
    payload = {
        "intent": "itinerary_generation",
        "trip_summary": {
            "duration_days": 3,
            "budget": 1000,
            "currency": "EUR",
        },
        "flights": {"data": [], "source": "live_api", "status": "unavailable"},
        "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
        "food_recommendations": [],
        "itinerary": [],
        "budget_breakdown": {},
    }
    payload.update(overrides)
    return payload


def test_budget_separates_verified_live_prices_from_model_estimates():
    payload = _itinerary_payload(
        flights={
            "data": [{"price": "EUR 450"}, {"price": "EUR 300"}],
            "source": "live_api",
            "status": "available",
        },
        hotels={
            "data": [
                {"price_total": 240, "currency": "EUR"},
                {"price_per_night": 80, "currency": "EUR"},
            ],
            "source": "live_api",
            "status": "available",
        },
        food_recommendations=[{"price_range": "low"}, {"price_range": "moderate"}],
        itinerary=[{"budget_eur": 20}, {"budget_eur": 30}],
    )

    budget = calculate_budget_breakdown(payload)

    assert budget["transport"] == 300
    assert budget["accommodation"] == 160
    assert budget["food"] == 82.5
    assert budget["activities"] == 50
    assert budget["total_known_cost"] == 460
    assert budget["model_estimated_total"] == 132.5
    assert budget["total_estimated_cost"] == 592.5
    assert budget["remaining_budget"] == 407.5
    assert budget["cost_details"]["transport"]["verified"] is True
    assert budget["cost_details"]["food"]["verified"] is False


def test_total_stay_price_is_not_multiplied_and_currency_mismatch_is_excluded():
    payload = _itinerary_payload(
        hotels={
            "data": [
                {
                    "price_total": 240,
                    "currency": "EUR",
                    "pricing_basis": "total_stay",
                },
                {
                    "price_total": 100,
                    "currency": "USD",
                    "pricing_basis": "total_stay",
                },
            ],
            "source": "live_api",
            "status": "available",
        }
    )

    budget = calculate_budget_breakdown(payload)

    assert budget["accommodation"] == 240
    assert budget["cost_details"]["accommodation"]["pricing_basis"] == "total_stay"
    assert any("USD-to-EUR" in warning for warning in budget["calculation_warnings"])


def test_static_flight_price_is_an_estimate_not_a_known_live_cost():
    payload = _itinerary_payload(
        flights={
            "data": [{"price": "EUR 120-200"}],
            "source": "static_fallback",
            "status": "available",
        }
    )

    budget = calculate_budget_breakdown(payload)

    assert budget["transport"] == 120
    assert budget["cost_details"]["transport"]["verified"] is False
    assert budget["total_known_cost"] == 0
    assert budget["model_estimated_total"] == budget["total_estimated_cost"]


def test_zero_cost_rounding_is_consistent_on_supported_python_versions():
    budget = calculate_budget_breakdown(_itinerary_payload())

    assert budget["total_known_cost"] == 0
    assert budget["cost_details"]["transport"]["amount"] == 0


def test_live_hotel_response_keeps_total_currency_and_true_nightly_price():
    hotel_data = {
        "property": {
            "priceBreakdown": {
                "grossPrice": {"value": 196.94, "currency": "EUR"}
            }
        }
    }

    price = HotelService()._extract_price(hotel_data, nights=2)

    assert price == {
        "price_per_night": 98.47,
        "price_total": 196.94,
        "currency": "EUR",
    }

    top_level_price = HotelService()._extract_price(
        {"priceBreakdown": {"grossPrice": {"value": 300, "currency": "EUR"}}},
        nights=3,
    )
    assert top_level_price["price_total"] == 300
    assert top_level_price["price_per_night"] == 100


def test_hotel_destination_connection_error_is_handled(monkeypatch):
    async def fail_get(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    result = asyncio.run(
        HotelService()._get_destination_id(
            "Paris",
            {"X-RapidAPI-Key": "test", "X-RapidAPI-Host": "test"},
        )
    )

    assert result is None


def test_selected_live_event_price_replaces_its_model_activity_estimate():
    payload = _itinerary_payload(
        local_events={
            "data": [{"name": "Jazz Night", "price": "EUR 30-50"}],
            "source": "live_api",
            "status": "available",
        },
        itinerary=[
            {"activity": "Attend Jazz Night", "budget_eur": 80},
            {"activity": "Museum visit", "budget_eur": 20},
        ],
    )

    budget = calculate_budget_breakdown(payload)

    assert budget["cost_details"]["events"]["amount"] == 30
    assert budget["cost_details"]["events"]["verified"] is True
    assert budget["activities"] == 20
    assert budget["total_known_cost"] == 30


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("one day in Berlin", 470),
        ("3-day trip to Paris", 710),
        ("7 day itinerary", 1190),
        ("10-day itinerary", 1550),
    ],
)
def test_generation_token_budget_scales_with_trip_length(message, expected):
    assert safe_max_new_tokens(None, message) == expected


def test_itinerary_api_requests_run_concurrently_and_hotels_are_fetched_once():
    class StubServices:
        hotel_calls = 0

        async def get_current_weather(self, _destination):
            await asyncio.sleep(0.08)
            return {"temperature": 20}

        async def search_flights(self, **_kwargs):
            await asyncio.sleep(0.08)
            return None

        async def search_hotels(self, *_args):
            self.hotel_calls += 1
            await asyncio.sleep(0.08)
            return [{"name": "Live Hotel", "price_total": 150, "currency": "EUR"}]

        async def search_events(self, *_args):
            await asyncio.sleep(0.08)
            return [{"name": "Live Event"}]

    stubs = StubServices()
    service = ApiContextService()
    service._weather_service = stubs
    service._flight_service = stubs
    service._hotel_service = stubs
    service._events_service = stubs

    started = perf_counter()
    context = asyncio.run(
        service.build_api_context_from_message(
            "Plan a 3-day trip from Berlin to Paris",
            {},
        )
    )
    elapsed = perf_counter() - started

    assert elapsed < 0.25
    assert stubs.hotel_calls == 1
    assert context["hotel_source"] == "live_api"
    assert context["flight_source"] == "static_fallback"


def test_common_missing_colon_model_typo_is_repaired_without_retry():
    raw = (
        '{"itinerary":[{"day":1,"time":"Morning","activity":"Walk","budget_eur":0}],'
        '"food_recommendations [{"name":"Cafe","price_range":"low","type":"cafe"}]}'
    )

    parsed = safe_json_loads_from_model(raw)

    assert parsed["food_recommendations"][0]["name"] == "Cafe"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('prefix {"key":{"nested":1}} trailing text', True),
        ('{"key":"a } inside a string"}', True),
        ('{"key":{"nested":1}', False),
        ('no object', False),
    ],
)
def test_complete_json_detection(text, expected):
    assert has_complete_json_object(text) is expected


def test_client_context_is_never_promoted_to_live_api_data():
    service = ApiContextService()
    context = asyncio.run(
        service.build_api_context_from_message(
            "Plan a trip",
            {
                "flights": [{"price": "EUR 1"}],
                "hotels": [{"price_total": 1, "currency": "EUR"}],
            },
        )
    )

    assert context["flight_source"] == "provided_context"
    assert context["hotel_source"] == "provided_context"
    assert any("unverified" in warning for warning in context["warnings"])


def test_word_duration_and_numeric_budget_are_extracted_exactly():
    travel_info = ApiContextService().extract_travel_info(
        "Plan a one day trip from Berlin to Paris under EUR 450"
    )

    assert travel_info["duration_days"] == 1
    assert travel_info["budget"] == 450


def test_itinerary_normalization_keeps_exactly_three_slots_per_day():
    payload = {
        "itinerary": [
            {"day": 1, "time": "Early Morning", "activity": "Walk"},
            {"day": 1, "time": "Morning", "activity": "Duplicate"},
            {"day": 1, "time": "Afternoon", "activity": "Museum"},
            {"day": 1, "time": "Evening", "activity": "Dinner"},
            {"day": 2, "time": "Morning", "activity": "Outside trip"},
        ]
    }

    normalized = normalize_itinerary_slots(payload, 1)

    assert [item["activity"] for item in normalized["itinerary"]] == ["Walk", "Museum", "Dinner"]
    assert itinerary_has_required_days(normalized, 1) is True
