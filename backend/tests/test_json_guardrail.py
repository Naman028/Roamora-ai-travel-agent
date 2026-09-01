from __future__ import annotations

from backend.fine_tuning.scripts.json_guardrail import (
    ModelDashboardResponse,
    enforce_api_context_truth,
    safe_parse_and_normalize,
)


def _payload(**dashboard_overrides):
    dashboard = {
        "intent": "itinerary_generation",
        "trip_summary": {
            "destination": "Paris",
            "duration_days": 3,
            "budget": 300,
        },
        "food_recommendations": [],
        "itinerary": [
            {"day": 1, "time": "Morning", "activity": "Walk the old town"}
        ],
        "budget_breakdown": {},
        "dashboard_actions": [],
        "api_grounding": {"used_api": [], "missing_api": [], "warnings": []},
    }
    dashboard.update(dashboard_overrides)
    return {
        "assistant_message": "Here is a plan based on live data.",
        "dashboard_payload": dashboard,
    }


def _empty_context():
    return {"weather": None, "flights": [], "hotels": [], "local_events": []}


def test_static_response_removes_hallucinated_data_and_adds_warnings():
    payload = _payload(
        food_recommendations=[
            {
                "name": "Neighbourhood Cafe",
                "type": "cafe",
                "rating": 4.9,
                "price": "EUR 12",
                "price_range": "cheap",
                "source": "Google Reviews",
            },
            {
                "name": "Luxury Restaurant",
                "type": "restaurant",
                "rating": 5,
                "price_range": "luxury",
                "source": "TripAdvisor",
            },
            {"name": "Castle Viewpoint", "type": "viewpoint", "rating": 4.8},
        ]
    )

    result = enforce_api_context_truth(payload, _empty_context(), "cheap Paris trip")
    dashboard = result["dashboard_payload"]
    recommendations = dashboard["food_recommendations"]
    warnings = dashboard["api_grounding"]["warnings"]

    assert [item["name"] for item in recommendations] == ["Neighbourhood Cafe"]
    assert recommendations[0]["rating"] is None
    assert recommendations[0]["source"] == "static_model_suggestion"
    assert recommendations[0]["price_range"] == "low"
    assert "price" not in recommendations[0]
    assert "based on live data" not in result["assistant_message"].lower()
    assert any("intentionally not invented" in warning for warning in warnings)
    assert len(warnings) == len({warning.lower() for warning in warnings})


def test_api_grounding_uses_only_the_provided_live_context():
    weather = {"city": "Berlin", "temperature_c": 18}
    hotel = {"name": "Verified Hotel", "price_total": 120, "currency": "EUR"}
    context = {
        "weather": weather,
        "weather_source": "live_api",
        "flights": [],
        "hotels": [hotel],
        "hotel_source": "live_api",
        "local_events": [],
    }
    payload = _payload(
        flight_options=[{"airline": "Invented Air"}],
        stay_recommendations=[{"name": "Invented Hotel"}],
    )

    dashboard = enforce_api_context_truth(payload, context)["dashboard_payload"]
    grounding = dashboard["api_grounding"]

    assert dashboard["weather"] == {
        "data": weather,
        "source": "live_api",
        "status": "available",
    }
    assert dashboard["hotels"]["data"] == [hotel]
    assert dashboard["flights"]["data"] == []
    assert set(grounding["used_api"]) == {"weather", "hotels"}
    assert set(grounding["missing_api"]) == {"flights", "events"}
    assert "flight_options" not in dashboard
    assert "stay_recommendations" not in dashboard


def test_budget_contains_only_known_costs_in_one_currency():
    payload = _payload(
        budget_breakdown={
            "transport": "50 EUR",
            "accommodation": "100 EUR",
            "food": "60 EUR",
            "activities": 40,
            "total": 250,
        },
        currency_exchange_rate=0,
    )

    dashboard = enforce_api_context_truth(payload, _empty_context())["dashboard_payload"]
    budget = dashboard["budget_breakdown"]

    assert budget["transport"] is None
    assert budget["accommodation"] is None
    assert budget["food"] == "60 EUR"
    assert budget["activities"] == 40
    assert budget["total_known_cost"] == 0
    assert budget["total_estimated_cost"] == 100
    assert budget["currency"] == "EUR"
    assert "total" not in budget
    assert "currency_exchange_rate" not in dashboard


def test_itinerary_removes_only_exact_duplicates():
    payload = _payload(
        itinerary=[
            {"day": 1, "time": "Morning", "activity": "Visit the Louvre"},
            {"day": 1, "time": "Morning", "activity": "Visit the Louvre"},
            {"day": 1, "time": "Evening", "activity": "Visit the Louvre"},
            {"day": 2, "time": "Morning", "activity": "Visit the Louvre"},
        ]
    )

    itinerary = enforce_api_context_truth(payload, _empty_context())["dashboard_payload"][
        "itinerary"
    ]

    assert len(itinerary) == 3
    assert {(item["day"], item["time"]) for item in itinerary} == {
        (1, "Morning"),
        (1, "Evening"),
        (2, "Morning"),
    }


def test_parser_returns_the_canonical_dashboard_schema():
    raw_output = """Model output:
    {
      "assistant_message": "Plan ready",
      "dashboard_payload": {
        "trip_summary": {"destination": "Rome"},
        "itinerary": [],
      },
    }
    """

    result = safe_parse_and_normalize(raw_output)
    dashboard = result["dashboard_payload"]

    ModelDashboardResponse.model_validate(result)
    assert dashboard["schema_version"] == "travel_dashboard_v1"
    assert set(dashboard["api_grounding"]) == {"used_api", "missing_api", "warnings"}
    for field in ("weather", "flights", "hotels", "local_events"):
        assert set(dashboard[field]) == {"data", "source", "status"}
    for legacy_field in (
        "flight",
        "flight_options",
        "stay_recommendations",
        "hotel_options",
        "weather_data",
        "events",
    ):
        assert legacy_field not in dashboard
