from __future__ import annotations

import gc
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Optional

DEBUG_RAW_OUTPUT = os.getenv("WANDERLY_DEBUG_RAW_OUTPUT", "false").lower() == "true"
MOCK_MODEL = os.getenv("WANDERLY_MOCK_MODEL", "false").lower() == "true"

# City aliases for hotel search
CITY_ALIASES = {
    "sttugart": "Stuttgart",
    "stuttgart": "Stuttgart", 
    "berlin": "Berlin",
    "munich": "Munich",
    "münchen": "Munich",
    "heidelberg": "Heidelberg"
}

from backend.app.models.chat_models import (
    ChatRequest,
    ChatResponse,
    DashboardPayload,
    ModelVariant,
)
from backend.app.core.observability import log_operation_failure
from backend.app.services.budget_service import calculate_budget_breakdown
from backend.app.services.hotel_service import HotelService

# Global hotel service instance
hotel_service = HotelService()


logger = logging.getLogger("wanderly.model")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
FINE_TUNING_SCRIPTS = BACKEND_ROOT / "fine_tuning" / "scripts"
if str(FINE_TUNING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FINE_TUNING_SCRIPTS))

from adapter_loader import (  # noqa: E402
    AdapterConfig,
    generate_text,
    load_base_model_and_adapter,
    load_base_model_only,
)
from json_guardrail import (  # noqa: E402
    build_safe_fallback_response,
    enforce_api_context_truth,
    safe_parse_and_normalize,
)


@dataclass
class LoadedModel:
    variant: ModelVariant
    tokenizer: Any
    model: Any


_loaded_model: LoadedModel | None = None
_model_lock = Lock()

_INCOMPLETE_JSON_ERRORS = (
    "no complete JSON object found",
    "no JSON object found",
)

# ── Token budget constants ────────────────────────────────────────────────────
DEFAULT_MAX_NEW_TOKENS = 800
MIN_MAX_NEW_TOKENS = 384
MAX_ALLOWED_NEW_TOKENS = 1800


def infer_duration_days(message: str) -> int | None:
    """Extract trip duration (days) from the user message, or return None."""
    match = re.search(r"\b(\d+)\s*[- ]?\s*day\b", message.lower())
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None

    word_match = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?\s*day\b",
        message.lower(),
    )
    if not word_match:
        return None
    return {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }[word_match.group(1)]


def safe_max_new_tokens(value: int | None, message: str = "") -> int:
    """Return a clamped token budget: duration-aware default when value is None."""
    if value is None:
        duration = infer_duration_days(message)
        if duration:
            return min(MAX_ALLOWED_NEW_TOKENS, 350 + (duration * 120))
        return 650

    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_NEW_TOKENS

    return max(MIN_MAX_NEW_TOKENS, min(value, MAX_ALLOWED_NEW_TOKENS))


def preload_model(model_variant: ModelVariant = "fine_tuned") -> None:
    config = AdapterConfig.from_env()
    logger.info("Preloading model variant=%s", model_variant)
    _get_model(model_variant, config)


def unload_models() -> None:
    _unload_current_model()


def _has_live_data(value: Any) -> bool:
    """Check if API data contains real live data."""
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return bool(value)


def normalize_flight_date(date_str: Optional[str], default_date: str) -> str:
    """Normalize flight date to YYYY-MM-DD format with fallback."""
    if not date_str:
        return default_date
    
    try:
        # Try to parse different date formats
        if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
            return date_str  # Already in correct format
        elif re.match(r'\d{2}/\d{2}/\d{4}', date_str):
            # Convert DD/MM/YYYY to YYYY-MM-DD
            day, month, year = date_str.split('/')
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        else:
            return default_date
    except Exception:
        return default_date


def normalize_flights_result(flight_result: Any) -> list[dict]:
    """Normalize flight service result to list of dictionaries."""
    if not flight_result:
        return []
    
    if isinstance(flight_result, list):
        return [flight.dict() if hasattr(flight, 'dict') else flight for flight in flight_result]
    elif isinstance(flight_result, dict) and "data" in flight_result:
        flight_data = flight_result["data"]
        if isinstance(flight_data, list):
            return flight_data
        elif flight_data:
            return [flight_data]
    
    return []


def build_static_fallback_flights(origin: str, destination: str, departure_date: str, return_date: Optional[str] = None) -> list[dict]:
    """Build static fallback flight options when API is unavailable."""
    return [
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "price": "EUR 250-350",
            "airline": "Emirates Airline",
            "flight_number": "EK-2026"
        },
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "price": "EUR 180-260",
            "airline": "Lufthansa",
            "flight_number": "LH-2031"
        },
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "price": "EUR 120-200",
            "airline": "Eurowings",
            "flight_number": "EW-4512"
        }
    ]


def is_unknown_city(value) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in ["", "unknown", "none", "null"]


def city_matches(a, b) -> bool:
    if is_unknown_city(a) or is_unknown_city(b):
        return False
    return str(a).strip().lower() == str(b).strip().lower()


def flights_match_route(flights, origin, destination) -> bool:
    if not isinstance(flights, list) or not flights:
        return False

    for flight in flights:
        if isinstance(flight, dict):
            f_origin = flight.get("origin")
            f_dest = flight.get("destination")
        else:
            f_origin = getattr(flight, "origin", None)
            f_dest = getattr(flight, "destination", None)

        if not city_matches(f_origin, origin):
            return False

        if not city_matches(f_dest, destination):
            return False

    return True


async def ensure_flights_for_route(payload: dict, api_context: dict, origin: Optional[str], destination: Optional[str]) -> dict:
    """Ensure flights are always present for valid origin/destination routes."""
    if not payload:
        payload = {}

    trip_summary = payload.get("trip_summary") or {}

    origin = (
        trip_summary.get("origin")
        or origin
        or api_context.get("travel_info", {}).get("origin")
    )

    destination = (
        trip_summary.get("destination")
        or destination
        or api_context.get("travel_info", {}).get("destination")
    )

    if not origin or not destination or is_unknown_city(destination):
        payload["flights"] = {
            "data": [],
            "source": "live_api",
            "status": "unavailable"
        }
        return payload

    travel_info = api_context.get("travel_info") or {}

    departure_date = normalize_flight_date(travel_info.get("departure_date"), "14/10/2026")
    return_date = normalize_flight_date(travel_info.get("return_date"), "17/10/2026")

    flights_list = []

    # 1. Prefer api_context flights if they match the route
    existing_flights = api_context.get("flights")

    if flights_match_route(existing_flights, origin, destination):
        flights_list = existing_flights
        flight_source = api_context.get("flight_source", "live_api")
    else:
        flights_list = []
        flight_source = "static_fallback"
        # 2. Try live FlightService
        try:
            from backend.app.services.flight_service import FlightService
            flight_service = FlightService()

            flight_result = await flight_service.search_flights(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                return_date=return_date
            )

            flights_list = normalize_flights_result(flight_result)
            flight_source = "live_api" if flights_list else "static_fallback"

        except Exception as exc:
            log_operation_failure(
                logger, operation="ensure_flights", error=exc
            )
            flights_list = []
            flight_source = "static_fallback"

    # 3. If live data empty, create static fallback
    if not flights_list:
        flights_list = build_static_fallback_flights(origin, destination, departure_date, return_date)
        flight_source = "static_fallback"

    payload["flights"] = {
        "data": flights_list,
        "source": flight_source,
        "status": "available"
    }

    # Add show_flights action
    actions = payload.get("dashboard_actions") or []
    for action in ["show_trip_summary", "show_itinerary", "show_budget"]:
        if action not in actions:
            actions.append(action)

    if "show_flights" not in actions:
        actions.append("show_flights")

    payload["dashboard_actions"] = actions

    # Fix api_grounding
    grounding = payload.get("api_grounding") or {}
    used_api = grounding.get("used_api") or []
    missing_api = grounding.get("missing_api") or []
    warnings = grounding.get("warnings") or []

    if flight_source == "live_api":
        if "flights" not in used_api:
            used_api.append("flights")
        missing_api = [x for x in missing_api if x != "flights"]
    else:
        if "flights" not in missing_api:
            missing_api.append("flights")
        if "Live flight API unavailable; showing static fallback flight options." not in warnings:
            warnings.append("Live flight API unavailable; showing static fallback flight options.")

    payload["api_grounding"] = {
        "used_api": used_api,
        "missing_api": missing_api,
        "warnings": warnings
    }

    logger.info(
        "[ENSURE FLIGHTS FINAL] source=%s count=%s",
        flight_source,
        len(flights_list),
    )

    return payload


def _build_intent_aware_mock_response(request: ChatRequest, api_context: dict[str, Any]) -> ChatResponse:
    """Build intent-aware mock response that matches expected structure."""
    # Detect intent from message
    message_lower = request.message.lower()
    
    # Itinerary keywords (highest priority)
    itinerary_keywords = ["plan", "itinerary", "trip", "day", "days", "budget trip", "travel plan", "schedule", "route", "vacation", "weekend trip"]
    
    # Weather intent detection
    weather_keywords = ["weather", "temperature", "rain", "sunny", "cloudy", "forecast"]
    if any(keyword in message_lower for keyword in weather_keywords):
        return ChatResponse(
            session_id=request.session_id,
            selected_model=request.model_variant,
            adapter_loaded=False,  # Always false in mock mode
            parse_success=True,
            fallback_used=False,
            retry_used=False,
            assistant_message="Here is the current weather information for your requested location.",
            dashboard_payload={
                "schema_version": "travel_dashboard_v1",
                "intent": "weather_query",
                "weather": {
                    "data": {
                        "location": "Stuttgart",
                        "temperature": 22.5,
                        "condition": "partly_cloudy",
                        "humidity": 65,
                        "wind_speed": 12.3
                    },
                    "source": "mock_api",
                    "status": "available"
                },
                "flights": {"data": [], "source": "mock_api", "status": "unavailable"},
                "hotels": {"data": [], "source": "mock_api", "status": "unavailable"},
                "local_events": {"data": [], "source": "mock_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": 0,
                    "food": 0,
                    "activities": 0,
                    "accommodation": 0,
                    "intercity_transport": 0,
                    "total_known_cost": 0,
                    "total": 0,
                    "remaining_budget": 0,
                    "within_budget": True,
                    "note": None,
                    "source": "empty_budget"
                },
                "dashboard_actions": ["show_weather"],
                "assistant_message_source": "mock_model",
                "api_grounding": {
                    "used_api": ["weather"],
                    "missing_api": ["flights", "hotels", "events"],
                    "warnings": []
                }
            }
        )
    
    # Itinerary intent detection (highest priority)
    if any(keyword in message_lower for keyword in itinerary_keywords):
        # Create mock itinerary response
        mock_response = ChatResponse(
            session_id=request.session_id,
            selected_model=request.model_variant,
            adapter_loaded=False,  # Always false in mock mode
            parse_success=True,
            fallback_used=False,
            retry_used=False,
            assistant_message="Here is your travel itinerary for the requested trip.",
            dashboard_payload={
                "schema_version": "travel_dashboard_v1",
                "intent": "itinerary_generation",
                "trip_summary": {
                    "destination": "Heidelberg",
                    "duration_days": 3,
                    "travelers": "solo",
                    "budget": "budget",
                    "origin": "Stuttgart",
                    "currency": "EUR",
                    "source": "backend_extraction"
                },
                "weather": {"data": None, "source": "mock_api", "status": "unavailable"},
                "flights": {"data": [], "source": "mock_api", "status": "unavailable"},
                "hotels": {"data": [], "source": "mock_api", "status": "unavailable"},
                "local_events": {"data": [], "source": "mock_api", "status": "unavailable"},
                "food_recommendations": [
                    {"name": "Local Restaurant", "price_range": "moderate"},
                    {"name": "Traditional Café", "price_range": "low"}
                ],
                "itinerary": [
                    {"day": 1, "time": "Morning", "activity": "Arrival in Heidelberg", "budget_eur": 50},
                    {"day": 1, "time": "Afternoon", "activity": "City exploration", "budget_eur": 25}
                ],
                "budget_breakdown": {},
                "dashboard_actions": ["show_trip_summary", "show_itinerary", "show_budget"],
                "assistant_message_source": "mock_model",
                "api_grounding": {
                    "used_api": [],
                    "missing_api": ["weather", "flights", "hotels", "events"],
                    "warnings": []
                }
            }
        )
        
        # Apply budget calculation for mock itinerary response
        mock_dashboard = mock_response.dashboard_payload.model_dump()
        mock_dashboard["budget_breakdown"] = calculate_budget_breakdown(mock_dashboard)
        mock_response.dashboard_payload = DashboardPayload.model_validate(mock_dashboard)
        
        return mock_response
    
    # Flight intent detection (lower priority - explicit flight words only)
    flight_keywords = ["flight", "fly", "airplane", "airport", "airfare", "ticket", "plane"]
    if any(keyword in message_lower for keyword in flight_keywords) and not any(keyword in message_lower for keyword in itinerary_keywords):
        return ChatResponse(
            session_id=request.session_id,
            selected_model=request.model_variant,
            adapter_loaded=False,
            parse_success=True,
            fallback_used=False,
            retry_used=False,
            assistant_message="Here are the available flight options for your requested route.",
            dashboard_payload={
                "schema_version": "travel_dashboard_v1",
                "intent": "flight_search",
                "weather": {"data": None, "source": "mock_api", "status": "unavailable"},
                "flights": {
                    "data": [
                        {
                            "origin": "Berlin",
                            "destination": "Munich",
                            "departure_date": "11/05/2026",
                            "return_date": "12/05/2026",
                            "price": "EUR 250-350",
                            "airline": "Emirates Airline",
                            "flight_number": "EK-2026"
                        },
                        {
                            "origin": "Berlin",
                            "destination": "Munich",
                            "departure_date": "11/05/2026",
                            "return_date": "12/05/2026",
                            "price": "EUR 180-260",
                            "airline": "Lufthansa",
                            "flight_number": "LH-2031"
                        },
                        {
                            "origin": "Berlin",
                            "destination": "Munich",
                            "departure_date": "11/05/2026",
                            "return_date": "12/05/2026",
                            "price": "EUR 120-200",
                            "airline": "Eurowings",
                            "flight_number": "EW-4512"
                        }
                    ],
                    "source": "mock_api",
                    "status": "available"
                },
                "hotels": {"data": [], "source": "mock_api", "status": "unavailable"},
                "local_events": {"data": [], "source": "mock_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": 0,
                    "food": 0,
                    "activities": 0,
                    "accommodation": 0,
                    "intercity_transport": 0,
                    "total_known_cost": 0,
                    "total": 0,
                    "remaining_budget": 0,
                    "within_budget": True,
                    "note": None,
                    "source": "empty_budget"
                },
                "dashboard_actions": ["show_flights"],
                "assistant_message_source": "mock_model",
                "api_grounding": {
                    "used_api": ["flights"],
                    "missing_api": ["weather", "hotels", "events"],
                    "warnings": []
                }
            }
        )
    
    # Hotel intent detection
    hotel_keywords = ["hotel", "stay", "accommodation", "room", "heidelberg"]
    if any(keyword in message_lower for keyword in hotel_keywords):
        return ChatResponse(
            session_id=request.session_id,
            selected_model=request.model_variant,
            adapter_loaded=False,
            parse_success=True,
            fallback_used=False,
            retry_used=False,
            assistant_message="Here are the available hotel options for your requested destination.",
            dashboard_payload={
                "schema_version": "travel_dashboard_v1",
                "intent": "hotel_search",
                "weather": {"data": None, "source": "mock_api", "status": "unavailable"},
                "flights": {"data": [], "source": "mock_api", "status": "unavailable"},
                "hotels": {
                    "data": [
                        {
                            "name": "Hotel Heidelberg",
                            "location": "Heidelberg",
                            "price_range": "moderate",
                            "rating": 4.2
                        }
                    ],
                    "source": "mock_api",
                    "status": "available"
                },
                "local_events": {"data": [], "source": "mock_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": 0,
                    "food": 0,
                    "activities": 0,
                    "accommodation": 0,
                    "intercity_transport": 0,
                    "total_known_cost": 0,
                    "total": 0,
                    "remaining_budget": 0,
                    "within_budget": True,
                    "note": None,
                    "source": "empty_budget"
                },
                "dashboard_actions": ["show_hotels"],
                "assistant_message_source": "mock_model",
                "api_grounding": {
                    "used_api": ["hotels"],
                    "missing_api": ["weather", "flights", "events"],
                    "warnings": []
                }
            }
        )
    
    # Default: itinerary generation
    travel_info = api_context.get("travel_info", {})
    destination = travel_info.get("destination", "Unknown")
    duration = travel_info.get("duration_days", 2)
    budget = travel_info.get("budget", "budget")
    origin = travel_info.get("origin", "Berlin")
    
    # Build descriptive message
    if origin != "Berlin":
        travel_desc = f"{duration}-day trip from {origin} to {destination}"
    else:
        travel_desc = f"{duration}-day trip to {destination}"
    
    return ChatResponse(
        session_id=request.session_id,
        selected_model=request.model_variant,
        adapter_loaded=False,  # Always false in mock mode
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message=f"Mock response: API context orchestration completed successfully for {travel_desc}.",
        dashboard_payload={
            "schema_version": "travel_dashboard_v1",
            "intent": "itinerary_generation",
            "trip_summary": {
                "destination": destination,
                "duration_days": duration,
                "travelers": "solo",
                "budget": budget,
                "origin": origin,
                "currency": "EUR",
                "source": "backend_extraction"
            },
            "weather": {
                "data": api_context.get("weather"),
                "source": "mock_api",
                "status": "unavailable"
            },
            "flights": {
                "data": api_context.get("flights", []),
                "source": "mock_api",
                "status": "unavailable"
            },
            "hotels": {
                "data": api_context.get("hotels", []),
                "source": "mock_api",
                "status": "unavailable"
            },
            "local_events": {
                "data": api_context.get("local_events", []),
                "source": "mock_api",
                "status": "unavailable"
            },
            "food_recommendations": [
                {"name": "Local Restaurant", "price_range": "moderate"},
                {"name": "Traditional Café", "price_range": "low"}
            ],
            "itinerary": [
                {"day": 1, "time": "Morning", "activity": "City tour", "budget_eur": 50},
                {"day": 1, "time": "Afternoon", "activity": "Museum visit", "budget_eur": 25}
            ],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": 100,
                "food": 150,
                "activities": 75,
                "total": 325
            },
            "dashboard_actions": ["show_trip_summary", "show_itinerary", "show_budget"],
            "itinerary_source": "mock_model",
            "assistant_message_source": "mock_model",
            "api_grounding": {
                "used_api": [],
                "missing_api": ["weather", "flights", "hotels", "events"],
                "warnings": []
            }
        }
    )


def _build_mock_response(request: ChatRequest, api_context: dict[str, Any]) -> ChatResponse:
    """Build a mock response for testing orchestration without loading models"""
    # Extract travel information from api_context (populated by API context service)
    travel_info = api_context.get("travel_info", {})
    
    destination = travel_info.get("destination", "Unknown")
    duration = travel_info.get("duration_days", 2)
    budget = travel_info.get("budget", "budget")
    origin = travel_info.get("origin", "Berlin")
    
    # Build descriptive message
    if origin != "Berlin":
        travel_desc = f"{duration}-day trip from {origin} to {destination}"
    else:
        travel_desc = f"{duration}-day trip to {destination}"
    
    # Check for missing API keys in warnings
    warnings = api_context.get("warnings", [])
    weather_missing_key = "Weather service unavailable - API key missing" in warnings
    flights_missing_key = "FLIGHT_API_KEY not found" in warnings or "Flight service unavailable - API key missing" in warnings
    hotels_missing_key = "RAPIDAPI_KEY not found" in warnings or "Hotel service unavailable - API key missing" in warnings
    events_missing_key = "TICKETMASTER_API_KEY not found" in warnings or "Events service unavailable - API key missing" in warnings
    
    return ChatResponse(
        session_id=request.session_id,
        selected_model=request.model_variant,
        adapter_loaded=False,
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message=f"Mock response: API context orchestration completed successfully for {travel_desc}.",
        dashboard_payload={
            "schema_version": "travel_dashboard_v1",
            "intent": "itinerary_generation",
            "trip_summary": {
                "destination": destination,
                "duration_days": duration,
                "travelers": "solo",
                "budget": budget,
                "origin": origin,
                "currency": "EUR",
                "source": "backend_extraction"
            },
            "weather": {
                "data": api_context.get("weather"),
                "source": "live_api",
                "status": "missing_api_key" if weather_missing_key else ("available" if _has_live_data(api_context.get("weather")) else "unavailable")
            },
            "flights": {
                "data": api_context.get("flights", []),
                "source": "live_api",
                "status": "missing_api_key" if flights_missing_key else ("available" if _has_live_data(api_context.get("flights", [])) else "unavailable")
            },
            "hotels": {
                "data": api_context.get("hotels", []),
                "source": "live_api", 
                "status": "missing_api_key" if hotels_missing_key else ("available" if _has_live_data(api_context.get("hotels", [])) else "unavailable")
            },
            "local_events": {
                "data": api_context.get("local_events", []),
                "source": "live_api",
                "status": "missing_api_key" if events_missing_key else ("available" if _has_live_data(api_context.get("local_events", [])) else "unavailable")
            },
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "transport": "mock",
                "food": "mock", 
                "activities": "mock",
                "total": "mock"
            },
            "dashboard_actions": ["show_trip_summary", "show_itinerary"],
            "itinerary_source": "model_generated",
            "assistant_message_source": "mock_model",
            "api_grounding": {
                "used_api": api_context.get("used_apis", []),
                "missing_api": api_context.get("missing_apis", []),
                "warnings": api_context.get("warnings", []) + ["Mock model mode enabled for local testing."]
            }
        }
    )


async def generate_travel_response(request: ChatRequest) -> ChatResponse:
    logger.info("[GTR START] generate_travel_response entered")

    selected_model = request.model_variant or getattr(request, "selected_model", None) or "base"
    adapter_loaded = selected_model == "fine_tuned"

    backend_intent = detect_user_intent(request.message)
    logger.info("[BACKEND INTENT] %s", backend_intent)

    from backend.app.services.api_context_service import api_context_service

    enriched_api_context = await api_context_service.build_api_context_from_message(
        request.message,
        request.api_context
    )

    logger.info(
        "[API CONTEXT READY] used=%s missing=%s",
        enriched_api_context.get("used_apis", []),
        enriched_api_context.get("missing_apis", []),
    )

    # Direct responses: do not load model
    if backend_intent == "weather_query":
        logger.info("[WEATHER DIRECT RESPONSE] Bypassing model")
        return await _build_direct_weather_response(request, enriched_api_context)

    if backend_intent == "flight_search":
        logger.info("[FLIGHT DIRECT RESPONSE] Bypassing model")
        return await _build_direct_flight_response(request, enriched_api_context)

    if backend_intent == "hotel_search":
        logger.info("[HOTEL DIRECT RESPONSE] Bypassing model")
        return await _build_direct_hotel_response(request, enriched_api_context)

    if MOCK_MODEL:
        logger.info("[MOCK MODEL RESPONSE]")
        return _build_intent_aware_mock_response(request, enriched_api_context)

    config = AdapterConfig.from_env()

    travel_info = enriched_api_context.get("travel_info", {}) or {}
    duration_days = travel_info.get("duration_days", 3)

    try:
        duration_days_int = int(duration_days)
    except Exception:
        duration_days_int = 3

    generation_tokens = request.max_new_tokens or safe_max_new_tokens(
        request.max_new_tokens,
        request.message
    )

    logger.info("[FINAL GENERATION TOKENS] %s", generation_tokens)

    config = replace(config, model_max_new_tokens=generation_tokens)

    tokenizer, model = _get_model(selected_model, config)
    prompt = _build_prompt(request.message, enriched_api_context, backend_intent)

    raw_text = ""
    retry_raw_text = None
    retry_used = False
    parse_success = False
    fallback_used = False
    normalized = None

    try:
        logger.info("[MODEL GENERATION START] backend_intent=%s", backend_intent)
        raw_text = generate_text(tokenizer, model, prompt, config) or ""

        logger.info("[MODEL OUTPUT LENGTH] %s", len(raw_text))

        parsed = safe_json_loads_from_model(raw_text)
        dashboard_payload = parsed.get("dashboard_payload", parsed)
        dashboard_payload = coerce_model_dashboard_payload(
            dashboard_payload,
            backend_intent,
            enriched_api_context,
        )
        if backend_intent == "itinerary_generation":
            dashboard_payload = normalize_itinerary_slots(
                dashboard_payload,
                duration_days_int,
            )

        # Itinerary essential validation
        if backend_intent == "itinerary_generation":
            if not itinerary_has_required_days(dashboard_payload, duration_days_int):
                raise ValueError("itinerary_missing_required_days")
            
            # Check itinerary is not empty
            itinerary = dashboard_payload.get("itinerary") or []
            if not itinerary:
                raise ValueError("itinerary_empty")

        normalized_dashboard = normalize_model_dashboard_payload(
            dashboard_payload,
            backend_intent,
            enriched_api_context
        )

        normalized = {
            "assistant_message": parsed.get(
                "assistant_message",
                f"Here is a {duration_days_int}-day budget trip plan."
            ),
            "dashboard_payload": normalized_dashboard
        }

        normalized = enforce_api_context_truth(
            normalized,
            enriched_api_context,
            request.message
        )
        normalized["dashboard_payload"] = restore_model_display_sections(
            normalized["dashboard_payload"],
            normalized_dashboard,
        )

        parse_success = True
        fallback_used = False
        retry_used = False

        if backend_intent == "itinerary_generation":
            normalized["dashboard_payload"]["itinerary_source"] = "model_generated"

    except Exception as first_exc:
        logger.error(
            "[MODEL FIRST ATTEMPT FAILED] type=%s",
            type(first_exc).__name__,
        )

        if backend_intent == "itinerary_generation":
            retry_used = True

            retry_origin = travel_info.get("origin", "Origin")
            retry_destination = travel_info.get("destination", "Destination")

            retry_prompt = (
                "Return compact JSON only with itinerary and food_recommendations.\n"
                f"Create exactly {duration_days_int} days from {retry_origin} to {retry_destination}; "
                "Morning, Afternoon, Evening for every day. Use unique short activities.\n"
                "Each itinerary item: day, time, activity, budget_eur. Food: at most 3 items.\n"
                f"User request: {request.message}\n"
                'Shape: {"itinerary":[{"day":1,"time":"Morning","activity":"...","budget_eur":0}],'
                '"food_recommendations":[{"name":"...","price_range":"low","type":"food"}]}'
            )

            try:
                logger.info("[MODEL RETRY START]")
                retry_raw_text = generate_text(tokenizer, model, retry_prompt, config) or ""

                logger.info("[MODEL RETRY OUTPUT LENGTH] %s", len(retry_raw_text))

                retry_parsed = safe_json_loads_from_model(retry_raw_text)
                retry_dashboard_payload = retry_parsed.get("dashboard_payload", retry_parsed)
                retry_dashboard_payload = coerce_model_dashboard_payload(
                    retry_dashboard_payload,
                    backend_intent,
                    enriched_api_context,
                )
                retry_dashboard_payload = normalize_itinerary_slots(
                    retry_dashboard_payload,
                    duration_days_int,
                )

                if not itinerary_has_required_days(retry_dashboard_payload, duration_days_int):
                    raise ValueError("retry_itinerary_missing_required_days")
                
                # Check retry itinerary is not empty
                retry_itinerary = retry_dashboard_payload.get("itinerary") or []
                if not retry_itinerary:
                    raise ValueError("retry_itinerary_empty")

                retry_normalized_dashboard = normalize_model_dashboard_payload(
                    retry_dashboard_payload,
                    backend_intent,
                    enriched_api_context
                )

                normalized = {
                    "assistant_message": retry_parsed.get(
                        "assistant_message",
                        f"Here is a {duration_days_int}-day budget trip plan from {retry_origin} to {retry_destination}."
                    ),
                    "dashboard_payload": retry_normalized_dashboard
                }

                normalized = enforce_api_context_truth(
                    normalized,
                    enriched_api_context,
                    request.message
                )
                normalized["dashboard_payload"] = restore_model_display_sections(
                    normalized["dashboard_payload"],
                    retry_normalized_dashboard,
                )

                parse_success = True
                fallback_used = False
                retry_used = True

                normalized["dashboard_payload"]["itinerary_source"] = "model_generated_retry"

            except Exception as retry_exc:
                logger.error(
                    "[MODEL RETRY FAILED] type=%s",
                    type(retry_exc).__name__,
                )

                normalized = await build_static_fallback_from_context(
                    request=request,
                    backend_intent=backend_intent,
                    api_context=enriched_api_context
                )

                parse_success = False
                fallback_used = True
                retry_used = True

                normalized["dashboard_payload"]["intent"] = "itinerary_generation"
                normalized["dashboard_payload"]["itinerary"] = []
                normalized["dashboard_payload"]["food_recommendations"] = []
                normalized["dashboard_payload"]["itinerary_source"] = "model_failed"
                normalized["assistant_message"] = (
                    "The model could not generate a complete valid itinerary. "
                    "Please try again with fewer days or increase max_new_tokens."
                )

        else:
            normalized = await build_static_fallback_from_context(
                request=request,
                backend_intent=backend_intent,
                api_context=enriched_api_context
            )

            parse_success = False
            fallback_used = True
            retry_used = False

    # Final safety guard
    if normalized is None or not isinstance(normalized, dict) or "dashboard_payload" not in normalized:
        logger.error("[NORMALIZED GUARD] normalized missing, building default payload")

        normalized = {
            "assistant_message": "The model could not generate a valid response.",
            "dashboard_payload": build_default_dashboard_payload(backend_intent)
        }

        parse_success = False
        fallback_used = True

    dashboard_payload = normalized["dashboard_payload"]

    # For itinerary_generation:
    # keep model itinerary, inject API sections, calculate budget
    if backend_intent == "itinerary_generation":
        trip_summary = dashboard_payload.get("trip_summary") or {}

        origin = trip_summary.get("origin") or travel_info.get("origin")
        destination = trip_summary.get("destination") or travel_info.get("destination")

        dashboard_payload = merge_live_api_context_into_dashboard(
            dashboard_payload,
            enriched_api_context,
            backend_intent
        )

        dashboard_payload = await ensure_flights_for_route(
            dashboard_payload,
            enriched_api_context,
            origin,
            destination
        )

        dashboard_payload["budget_breakdown"] = calculate_budget_breakdown(dashboard_payload)
        dashboard_payload = update_dashboard_actions_for_available_sections(dashboard_payload)

        if "itinerary_source" not in dashboard_payload:
            dashboard_payload["itinerary_source"] = "model_generated" if parse_success else "model_failed"

        normalized["dashboard_payload"] = dashboard_payload

    logger.info(
        "[GTR RETURN] parse_success=%s fallback_used=%s retry_used=%s",
        parse_success,
        fallback_used,
        retry_used
    )

    return ChatResponse(
        session_id=request.session_id,
        selected_model=selected_model,
        adapter_loaded=adapter_loaded,
        parse_success=parse_success,
        fallback_used=fallback_used,
        retry_used=retry_used,
        assistant_message=normalized["assistant_message"],
        dashboard_payload=normalized["dashboard_payload"],
    )

def safe_json_loads_from_model(raw_text: str):
    """
    Safely parse JSON from model output.
    Repairs:
    - markdown fences
    - text before JSON
    - trailing accidental quotes
    - trailing commas
    - missing closing braces/brackets
    """
    if not raw_text or not str(raw_text).strip():
        raise ValueError("empty_model_output")

    text = str(raw_text).strip()

    text = text.replace("```json", "").replace("```", "").strip()

    first = text.find("{")
    if first == -1:
        raise ValueError("no_json_object_found")

    text = text[first:].strip()

    while len(text) > 1 and text.endswith('"') and text[-2] in ("}", "]"):
        text = text[:-1].strip()

    text = text.replace(",}", "}").replace(",]", "]")
    text = re.sub(
        r'"([A-Za-z_][A-Za-z0-9_]*)\s+(?=[\[{])',
        r'"\1":',
        text,
    )
    text = re.sub(
        r'"([A-Za-z_][A-Za-z0-9_]*)"\s+(?=[\[{])',
        r'"\1":',
        text,
    )

    def try_load(candidate: str):
        parsed = json.loads(candidate)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed

    try:
        return try_load(text)
    except Exception:
        pass

    stack = []
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()

    repaired = text + "".join(reversed(stack))
    repaired = repaired.replace(",}", "}").replace(",]", "]")

    logger.info("[SAFE JSON REPAIR COMPLETED] length=%s", len(repaired))

    try:
        return try_load(repaired)
    except Exception as exc:
        logger.error("[SAFE JSON REPAIR FAILED] type=%s", type(exc).__name__)
        raise ValueError("json_parse_failed") from exc

def parse_model_json_with_repair(raw_text: str) -> dict:
    """Parse model JSON with robust repair logic"""
    if not raw_text:
        raise ValueError("Empty model output")

    import json

    text = raw_text.strip()

    # Remove markdown fences
    text = text.replace("```json", "").replace("```", "").strip()

    # First try direct parse
    try:
        parsed = json.loads(text)
        # Sometimes model output is a JSON string containing JSON
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed
    except Exception:
        pass

    logger.info("[MODEL JSON REPAIR ATTEMPT]")

    # If output is accidentally quoted as a whole string, unquote once
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        try:
            text = json.loads(text)
        except Exception:
            text = text[1:-1]

    # Trim before first JSON object
    first = text.find("{")
    if first != -1:
        text = text[first:]

    # Remove accidental trailing quote after final brace/array
    # Example: ... }]}"  should become ... }]
    while len(text) > 1 and text.endswith('"') and text[-2] in ("}", "]"):
        text = text[:-1].strip()

    # Remove common trailing comma issues
    text = text.replace(",}", "}").replace(",]", "]")

    # Balance braces while ignoring braces inside strings
    open_braces = 0
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if not in_string:
            if ch == "{":
                open_braces += 1
            elif ch == "}":
                open_braces -= 1

    if open_braces > 0:
        text += "}" * open_braces

    logger.info("[MODEL JSON REPAIR BALANCED] length=%s", len(text))

    parsed = json.loads(text)

    if isinstance(parsed, str):
        parsed = json.loads(parsed)

    logger.info("[MODEL JSON REPAIR SUCCESS]")
    return parsed


def extract_live_section(api_context: dict, section: str):
    """Extract live API section from context with multiple key support"""
    # Direct keys
    if section in api_context:
        return api_context[section]
    
    # Suffix keys
    suffix_key = f"{section}_data"
    if suffix_key in api_context:
        return api_context[suffix_key]
    
    # Nested dashboard_payload
    if "dashboard_payload" in api_context:
        dashboard = api_context["dashboard_payload"]
        if section in dashboard:
            if isinstance(dashboard[section], dict) and "data" in dashboard[section]:
                return dashboard[section]["data"]
            return dashboard[section]

    # Nested api_context
    if "api_context" in api_context:
        nested = api_context["api_context"]
        if section in nested:
            return nested[section]

    # Nested data
    if "data" in api_context:
        data = api_context["data"]
        if isinstance(data, dict) and section in data:
            return data[section]

    # Special case for events/local_events
    if section == "events":
        for key in ["local_events", "events_data"]:
            if key in api_context:
                return api_context[key]

    return None


DESTINATION_COORDS = {
    "amsterdam": {"lat": 52.3676, "lng": 4.9041},
    "barcelona": {"lat": 41.3874, "lng": 2.1686},
    "budapest": {"lat": 47.4979, "lng": 19.0402},
    "heidelberg": {"lat": 49.3988, "lng": 8.6724},
    "london": {"lat": 51.5072, "lng": -0.1276},
    "los angeles": {"lat": 34.0522, "lng": -118.2437},
    "marrakech": {"lat": 31.6295, "lng": -7.9811},
    "miami": {"lat": 25.7617, "lng": -80.1918},
    "munich": {"lat": 48.1351, "lng": 11.5820},
    "paris": {"lat": 48.8566, "lng": 2.3522},
    "porto": {"lat": 41.1579, "lng": -8.6291},
    "prague": {"lat": 50.0755, "lng": 14.4378},
    "singapore": {"lat": 1.3521, "lng": 103.8198},
    "vienna": {"lat": 48.2082, "lng": 16.3738},
    "zurich": {"lat": 47.3769, "lng": 8.5417},
}


def _as_model_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("data"), list):
            return value["data"]
        for list_key in ("items", "results", "recommendations", "options"):
            if isinstance(value.get(list_key), list):
                return value[list_key]
        item_keys = {
            "name", "label", "title", "activity", "day", "time", "price",
            "price_range", "lat", "lng", "latitude", "longitude",
        }
        if item_keys.intersection(value.keys()):
            return [value]
        dict_items = [item for item in value.values() if item is not None]
        if dict_items:
            return dict_items
        return [value]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped in ("[]", "null"):
            return []
        try:
            parsed = json.loads(stripped)
            return _as_model_list(parsed)
        except Exception:
            return [stripped]
    return [value]


def _normalize_dashboard_action_list(actions: Any) -> list[str]:
    normalized: list[str] = []
    for action in _as_model_list(actions):
        value = None
        if isinstance(action, str):
            value = action
        elif isinstance(action, dict):
            value = action.get("type") or action.get("target") or action.get("label")
        if isinstance(value, str) and value.strip():
            normalized.append(value.strip())
    return list(dict.fromkeys(normalized))


def _normalize_model_api_section(value: Any, source: str = "model_generated") -> dict:
    data = _as_model_list(value)
    return {
        "data": data,
        "source": source,
        "status": "available" if data else "unavailable",
    }


def _normalize_map_data(payload: dict) -> dict:
    map_data = payload.get("map_data")
    if not isinstance(map_data, dict):
        map_data = {}

    markers = _as_model_list(
        map_data.get("markers")
        or map_data.get("places")
        or map_data.get("locations")
    )
    cleaned_markers = []
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        label = marker.get("label") or marker.get("name") or marker.get("title")
        lat = marker.get("lat") or marker.get("latitude")
        lng = marker.get("lng") or marker.get("lon") or marker.get("longitude")
        cleaned = {
            "label": label or "Map point",
            "type": marker.get("type") or "place",
        }
        if lat is not None and lng is not None:
            cleaned["lat"] = lat
            cleaned["lng"] = lng
        cleaned_markers.append(cleaned)

    trip_summary = payload.get("trip_summary") if isinstance(payload.get("trip_summary"), dict) else {}
    destination = str(trip_summary.get("destination") or "").strip()
    coords = DESTINATION_COORDS.get(destination.lower())
    if not cleaned_markers and coords:
        cleaned_markers.append({
            "label": destination,
            "lat": coords["lat"],
            "lng": coords["lng"],
            "type": "destination",
        })

    return {
        "markers": cleaned_markers,
        "route_segments": _as_model_list(map_data.get("route_segments") or map_data.get("routes")),
    }


def coerce_model_dashboard_payload(payload: dict, backend_intent: str, api_context: dict) -> dict:
    """Accept common model output aliases before strict validation/guardrails."""
    if not isinstance(payload, dict):
        payload = {}

    payload = dict(payload)

    trip_summary = payload.get("trip_summary")
    if isinstance(trip_summary, str):
        payload["trip_summary"] = {"summary": trip_summary}
    elif not isinstance(trip_summary, dict):
        payload["trip_summary"] = {}

    for nested_key in (
        "itinerary",
        "food_recommendations",
        "budget_breakdown",
        "dashboard_actions",
        "api_grounding",
        "map_data",
    ):
        if nested_key not in payload and isinstance(payload["trip_summary"], dict):
            nested_value = payload["trip_summary"].pop(nested_key, None)
            if nested_value is not None:
                payload[nested_key] = nested_value

    if "food_recommendations" not in payload:
        payload["food_recommendations"] = (
            payload.get("food")
            or payload.get("local_food")
            or payload.get("restaurants")
            or []
        )
    payload["food_recommendations"] = _as_model_list(payload.get("food_recommendations"))

    payload["itinerary"] = _as_model_list(payload.get("itinerary"))

    if not isinstance(payload.get("budget_breakdown"), dict):
        budget_value = payload.get("budget_breakdown")
        if isinstance(budget_value, str):
            try:
                parsed_budget = json.loads(budget_value)
                payload["budget_breakdown"] = parsed_budget if isinstance(parsed_budget, dict) else {}
            except Exception:
                payload["budget_breakdown"] = {}
        else:
            payload["budget_breakdown"] = {}

    hotel_alias = (
        payload.get("hotels")
        or payload.get("hotel_options")
        or payload.get("stay_recommendations")
        or payload.get("stays")
        or payload.get("hotel")
    )
    hotel_items = _as_model_list(hotel_alias)
    if hotel_items:
        payload["hotels"] = _normalize_model_api_section(hotel_items)
        payload["stay_recommendations"] = hotel_items
    else:
        payload.setdefault("stay_recommendations", [])

    flight_alias = payload.get("flights") or payload.get("flight_options") or payload.get("flight")
    flight_items = _as_model_list(flight_alias)
    if flight_items:
        payload["flights"] = _normalize_model_api_section(flight_items)

    event_alias = payload.get("local_events") or payload.get("events") or payload.get("event_options")
    event_items = _as_model_list(event_alias)
    if event_items:
        payload["local_events"] = _normalize_model_api_section(event_items)

    if "weather" not in payload and "weather_data" in payload:
        payload["weather"] = payload.get("weather_data")

    payload["map_data"] = _normalize_map_data(payload)
    payload["dashboard_actions"] = _normalize_dashboard_action_list(payload.get("dashboard_actions"))
    payload["schema_version"] = "travel_dashboard_v1"
    payload["intent"] = backend_intent

    return payload


def restore_model_display_sections(payload: dict, model_payload: dict) -> dict:
    """Restore non-API display fields that strict API truth normalization may drop."""
    if not isinstance(payload, dict) or not isinstance(model_payload, dict):
        return payload

    model_stays = _as_model_list(model_payload.get("stay_recommendations"))
    if not model_stays and isinstance(model_payload.get("hotels"), dict):
        model_stays = _as_model_list(model_payload["hotels"].get("data"))
    if model_stays and not payload.get("stay_recommendations"):
        payload["stay_recommendations"] = model_stays

    hotels = payload.get("hotels")
    if model_stays and (
        not isinstance(hotels, dict)
        or not hotels.get("data")
        or hotels.get("status") == "unavailable"
    ):
        payload["hotels"] = {
            "data": model_stays,
            "source": "model_generated",
            "status": "available",
        }

    if not payload.get("map_data") or not payload["map_data"].get("markers"):
        payload["map_data"] = _normalize_map_data(model_payload)
    else:
        payload["map_data"] = _normalize_map_data(payload)

    if not isinstance(payload.get("dashboard_actions"), list):
        payload["dashboard_actions"] = []
    return payload


async def _build_direct_weather_response(request: ChatRequest, enriched_api_context: dict) -> ChatResponse:
    """Build direct weather response bypassing model"""
    logger.info("[BUILD DIRECT WEATHER RESPONSE] Building deterministic weather response")
    logger.info(
        "[DIRECT WEATHER CONTEXT] weather_available=%s",
        bool(extract_live_section(enriched_api_context, "weather")),
    )

    # Extract location robustly
    location = "requested location"

    # Try travel_info first
    travel_info = enriched_api_context.get("travel_info", {})
    if "destination" in travel_info:
        location = travel_info["destination"]
    elif "location" in travel_info:
        location = travel_info["location"]

    # Parse from message "in [city]"
    if " in " in request.message.lower():
        after_in = request.message.lower().split(" in ", 1)[1].strip()
        if after_in:
            location = after_in.split()[0].title()

    # Check for specific cities in message
    if "stuttgart" in request.message.lower():
        location = "Stuttgart"
    elif "heidelberg" in request.message.lower():
        location = "Heidelberg"
    elif "berlin" in request.message.lower():
        location = "Berlin"
    elif "munich" in request.message.lower():
        location = "Munich"

    logger.info("[DIRECT WEATHER LOCATION] present=%s", bool(location))

    # Extract weather data using robust extractor
    weather_data = extract_live_section(enriched_api_context, "weather")

    # If no weather data, fetch it directly
    if not weather_data:
        try:
            from backend.app.services.weather_service import WeatherService
            weather_service = WeatherService()
            weather_data = await weather_service.get_current_weather(location)
            logger.info("[DIRECT WEATHER FETCHED] available=%s", bool(weather_data))
        except Exception as exc:
            log_operation_failure(
                logger, operation="direct_weather_fetch", error=exc
            )
            weather_data = None

    weather_status = "available" if weather_data else "unavailable"
    logger.info("[DIRECT WEATHER DATA] available=%s", bool(weather_data))

    # Build used_apis and warnings
    used_apis = ["weather"] if weather_data else []
    warnings = [] if weather_data else [f"Weather API returned no data for {location}"]

    return ChatResponse(
        session_id=request.session_id,
        selected_model=request.model_variant,
        adapter_loaded=request.model_variant == "fine_tuned",
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message=f"Here is current weather information for {location}.",
        dashboard_payload={
            "schema_version": "travel_dashboard_v1",
            "intent": "weather_query",
            "weather": {
                "data": weather_data,
                "source": "live_api",
                "status": weather_status
            },
            "flights": {"data": [], "source": "live_api", "status": "unavailable"},
            "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
            "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": 0,
                "food": 0,
                "activities": 0,
                "accommodation": 0,
                "intercity_transport": 0,
                "total_known_cost": 0,
                "total": 0,
                "remaining_budget": 0,
                "within_budget": True,
                "note": None,
                "source": "empty_budget"
            },
            "dashboard_actions": ["show_weather"],
            "api_grounding": {
                "used_api": used_apis,
                "missing_api": [],
                "warnings": warnings
            }
        },
    )


def normalize_model_dashboard_payload(payload: dict, backend_intent: str, api_context: dict) -> dict:
    """Normalize model output to proper schema format"""
    logger.info("[MODEL NORMALIZATION START]")
    payload = coerce_model_dashboard_payload(payload, backend_intent, api_context)
    
    # Ensure payload is a dict
    if not isinstance(payload, dict):
        payload = {}
    
    # Set required fields
    payload["schema_version"] = "travel_dashboard_v1"
    payload["intent"] = backend_intent
    
    # Ensure trip_summary exists and fill from api_context
    if "trip_summary" not in payload or not payload["trip_summary"]:
        payload["trip_summary"] = {}
    
    travel_info = api_context.get("travel_info", {})
    trip_summary = payload["trip_summary"]
    
    # Fill missing trip_summary fields from travel_info
    if "origin" not in trip_summary and "origin" in travel_info:
        trip_summary["origin"] = travel_info["origin"]
    if "destination" not in trip_summary and "destination" in travel_info:
        trip_summary["destination"] = travel_info["destination"]
    if "duration_days" not in trip_summary and "duration_days" in travel_info:
        trip_summary["duration_days"] = travel_info["duration_days"]
    if "budget" not in trip_summary and "budget" in travel_info:
        trip_summary["budget"] = travel_info["budget"]
    
    trip_summary.setdefault("currency", "EUR")
    trip_summary.setdefault("source", "model_generated")
    
    # Normalize weather section
    weather_data = extract_live_section(api_context, "weather")
    if weather_data:
        payload["weather"] = {
            "data": weather_data,
            "source": api_context.get("weather_source", "provided_context"),
            "status": "available"
        }
    else:
        # Convert model weather if present
        model_weather = payload.get("weather")
        if model_weather and isinstance(model_weather, dict) and "data" not in model_weather:
            payload["weather"] = {
                "data": model_weather,
                "source": "live_api",
                "status": "available"
            }
        else:
            payload["weather"] = {
                "data": None,
                "source": "live_api",
                "status": "unavailable"
            }
    
    # Normalize flights section
    # Preserve flights with source information
    if api_context.get("flights"):
        flight_source = api_context.get("flight_source", "live_api")
        payload["flights"] = {
            "data": api_context["flights"],
            "source": flight_source,
            "status": "available"
        }
    flights_data = extract_live_section(api_context, "flights")
    if flights_data:
        payload["flights"] = {
            "data": flights_data,
            "source": "live_api",
            "status": "available"
        }
    else:
        # Convert model flights if present
        model_flights = payload.get("flights")
        if model_flights and isinstance(model_flights, dict) and "data" not in model_flights:
            if model_flights.get("message") or model_flights == {}:
                payload["flights"] = {
                    "data": [],
                    "source": "live_api",
                    "status": "unavailable"
                }
            else:
                payload["flights"] = {
                    "data": model_flights,
                    "source": "live_api",
                    "status": "available"
                }
        else:
            payload["flights"] = {
                "data": [],
                "source": "live_api",
                "status": "unavailable"
            }
    
    # Normalize hotels section
    hotels_data = extract_live_section(api_context, "hotels")
    if hotels_data:
        payload["hotels"] = {
            "data": hotels_data,
            "source": "live_api",
            "status": "available"
        }
    else:
        # Convert model hotels if present
        model_hotels = payload.get("hotels")
        if model_hotels and isinstance(model_hotels, dict) and "data" not in model_hotels:
            if model_hotels.get("message") or model_hotels == {}:
                payload["hotels"] = {
                    "data": [],
                    "source": "live_api",
                    "status": "unavailable"
                }
            else:
                model_hotel_data = _as_model_list(model_hotels)
                payload["hotels"] = {
                    "data": model_hotel_data,
                    "source": "model_generated",
                    "status": "available"
                }
                payload["stay_recommendations"] = model_hotel_data
        else:
            payload["hotels"] = {
                "data": [],
                "source": "live_api",
                "status": "unavailable"
            }
    
    # Normalize local_events section
    events_data = extract_live_section(api_context, "events")
    if events_data:
        payload["local_events"] = {
            "data": events_data,
            "source": api_context.get("event_source", "provided_context"),
            "status": "available"
        }
    else:
        # Convert model events if present
        model_events = payload.get("local_events")
        if model_events and isinstance(model_events, dict) and "data" not in model_events:
            if model_events.get("message") or model_events == {}:
                payload["local_events"] = {
                    "data": [],
                    "source": "live_api",
                    "status": "unavailable"
                }
            else:
                payload["local_events"] = {
                    "data": model_events,
                    "source": "live_api",
                    "status": "available"
                }
        else:
            payload["local_events"] = {
                "data": [],
                "source": "live_api",
                "status": "unavailable"
            }
    
    # Ensure lists
    payload.setdefault("food_recommendations", [])
    if not isinstance(payload["food_recommendations"], list):
        payload["food_recommendations"] = _as_model_list(payload["food_recommendations"])
    
    payload.setdefault("itinerary", [])
    if not isinstance(payload["itinerary"], list):
        payload["itinerary"] = _as_model_list(payload["itinerary"])
    
    # Ensure map_data
    payload["map_data"] = _normalize_map_data(payload)
    
    # Ensure budget_breakdown
    payload.setdefault("budget_breakdown", {
        "currency": "EUR",
        "transport": None,
        "intercity_transport": None,
        "total_known_cost": 0,
        "note": None
    })
    
    # Set dashboard_actions based on intent
    if backend_intent == "weather_query":
        payload["dashboard_actions"] = ["show_weather"]
    elif backend_intent == "flight_search":
        payload["dashboard_actions"] = ["show_flights"]
    elif backend_intent == "hotel_search":
        payload["dashboard_actions"] = ["show_hotels"]
    elif backend_intent == "events_search":
        payload["dashboard_actions"] = ["show_events"]
    elif backend_intent == "itinerary_generation":
        dashboard_actions = _normalize_dashboard_action_list(payload.get("dashboard_actions"))
        for action in ["show_trip_summary", "show_itinerary", "show_budget"]:
            if action not in dashboard_actions:
                dashboard_actions.append(action)
        if payload.get("flights", {}).get("status") == "available":
            dashboard_actions.append("show_flights")
        if payload.get("hotels", {}).get("status") == "available":
            dashboard_actions.append("show_hotels")
        if payload.get("local_events", {}).get("status") == "available":
            dashboard_actions.append("show_events")
        payload["dashboard_actions"] = list(dict.fromkeys(dashboard_actions))
    else:
        payload["dashboard_actions"] = []
    
    # Build api_grounding
    used_apis = []
    if payload.get("weather", {}).get("status") == "available":
        used_apis.append("weather")
    if payload.get("flights", {}).get("status") == "available":
        used_apis.append("flights")
    if payload.get("hotels", {}).get("status") == "available":
        used_apis.append("hotels")
    if payload.get("local_events", {}).get("status") == "available":
        used_apis.append("events")
    
    payload["api_grounding"] = {
        "used_api": used_apis,
        "missing_api": [],
        "warnings": []
    }
    
    logger.info(
        "[MODEL NORMALIZATION VALIDATED] intent=%s action_count=%s",
        payload.get("intent"),
        len(payload.get("dashboard_actions", [])),
    )
    
    return payload


# City alias map for typo correction
CITY_ALIASES = {
    "sttugart": "Stuttgart",
    "stuttgart": "Stuttgart",
    "berlin": "Berlin",
    "munich": "Munich",
    "münchen": "Munich",
    "heidelberg": "Heidelberg"
}


def normalize_flights_result(flight_result):
    """Normalize flight service response to consistent list format"""
    if not flight_result:
        return []
    
    if isinstance(flight_result, list):
        return flight_result
    
    if isinstance(flight_result, dict):
        data = flight_result.get("data")
        if isinstance(data, list):
            return data
        if data:
            return [data]
    
    return []


def normalize_flight_date(date_value: str | None, fallback: str) -> str:
    """Normalize flight date from ISO format to dd/mm/yyyy"""
    if not date_value:
        return fallback
    
    # yyyy-mm-dd -> dd/mm/yyyy
    if isinstance(date_value, str) and "-" in date_value:
        parts = date_value.split("-")
        if len(parts) == 3:
            yyyy, mm, dd = parts
            return f"{dd}/{mm}/{yyyy}"
    
    return date_value


def build_static_fallback_flights(origin, destination, departure_date, return_date):
    """Build static fallback flight options when live API returns no data"""
    return [
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "price": "EUR 250-350",
            "airline": "Emirates Airline",
            "flight_number": "EK-2026"
        },
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "price": "EUR 180-260",
            "airline": "Lufthansa",
            "flight_number": "LH-2031"
        },
        {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "price": "EUR 120-200",
            "airline": "Eurowings",
            "flight_number": "EW-4512"
        }
    ]


ITINERARY_TIMES = ("Morning", "Afternoon", "Evening")


def normalize_itinerary_slots(payload: dict, duration_days: int) -> dict:
    """Keep exactly one valid Morning/Afternoon/Evening item for each trip day."""
    selected: dict[tuple[int, str], dict] = {}
    for raw_item in payload.get("itinerary") or []:
        if not isinstance(raw_item, dict):
            continue
        try:
            day = int(raw_item.get("day"))
        except (TypeError, ValueError):
            continue
        if not 1 <= day <= duration_days:
            continue

        raw_time = str(raw_item.get("time") or "").lower()
        time_label = next(
            (label for label in ITINERARY_TIMES if label.lower() in raw_time),
            None,
        )
        activity = str(raw_item.get("activity") or "").strip()
        if not time_label or not activity:
            continue

        key = (day, time_label)
        if key not in selected:
            item = dict(raw_item)
            item["day"] = day
            item["time"] = time_label
            item["activity"] = activity
            selected[key] = item

    normalized = dict(payload)
    normalized["itinerary"] = [
        selected[(day, time_label)]
        for day in range(1, duration_days + 1)
        for time_label in ITINERARY_TIMES
        if (day, time_label) in selected
    ]
    return normalized


def itinerary_has_required_days(payload: dict, duration_days: int) -> bool:
    """
    Check if itinerary contains all required days.
    Returns True if all days from 1 to duration_days are present.
    """
    itinerary = payload.get("itinerary") or []
    expected = duration_days * len(ITINERARY_TIMES)
    if len(itinerary) != expected:
        return False
    slots = {
        (int(item["day"]), str(item["time"]))
        for item in itinerary
        if isinstance(item, dict) and item.get("day") is not None and item.get("time")
    }
    return slots == {
        (day, time_label)
        for day in range(1, duration_days + 1)
        for time_label in ITINERARY_TIMES
    }


def itinerary_has_too_many_repeats(payload: dict, max_repeat: int = 1) -> bool:
    """
    Return True if the same activity appears more than max_repeat times.
    For long trips, exact same activity should not repeat.
    """
    if not payload or not isinstance(payload, dict):
        return False

    itinerary = payload.get("itinerary") or []
    counts = {}

    for item in itinerary:
        if not isinstance(item, dict):
            continue

        activity = str(item.get("activity") or "").strip().lower()
        if not activity:
            continue

        counts[activity] = counts.get(activity, 0) + 1

        if counts[activity] > max_repeat:
            return True

    return False


def itinerary_has_required_diversity(payload: dict, duration_days: int) -> bool:
    """
    Check if itinerary has sufficient unique activities.
    For duration_days days with 3 items per day, need at least duration_days * 2 unique activities.
    """
    if not payload or not isinstance(payload, dict):
        return False

    itinerary = payload.get("itinerary") or []
    activities = []

    for item in itinerary:
        if isinstance(item, dict):
            activity = str(item.get("activity") or "").strip().lower()
            if activity:
                activities.append(activity)

    if not activities:
        return False

    unique_count = len(set(activities))
    required_min_unique = min(len(activities), duration_days * 2)

    return unique_count >= required_min_unique


def update_dashboard_actions_for_available_sections(payload: dict) -> dict:
    """
    Update dashboard_actions based on available API data sections.
    Only adds show_flights, show_hotels, show_events when data is available.
    """
    if not payload or not isinstance(payload, dict):
        return payload

    intent = payload.get("intent")

    if intent != "itinerary_generation":
        return payload

    actions = _normalize_dashboard_action_list(payload.get("dashboard_actions"))
    for action in ["show_trip_summary", "show_itinerary", "show_budget"]:
        if action not in actions:
            actions.append(action)

    flights = payload.get("flights") or {}
    hotels = payload.get("hotels") or {}
    local_events = payload.get("local_events") or {}

    if isinstance(flights, dict) and flights.get("status") == "available" and flights.get("data"):
        actions.append("show_flights")

    if isinstance(hotels, dict) and hotels.get("status") == "available" and hotels.get("data"):
        actions.append("show_hotels")

    if isinstance(local_events, dict) and local_events.get("status") == "available" and local_events.get("data"):
        actions.append("show_events")

    # Remove duplicates while preserving order
    payload["dashboard_actions"] = list(dict.fromkeys(actions))

    return payload


def normalize_hotels_result(hotel_result):
    """Normalize hotel service result to list format"""
    if not hotel_result:
        return []
    
    if isinstance(hotel_result, list):
        return hotel_result
    
    if isinstance(hotel_result, dict):
        data = hotel_result.get("data") or hotel_result.get("hotels")
        if isinstance(data, list):
            return data
        if data:
            return [data]
    
    return []


async def _build_direct_hotel_response(request: ChatRequest, enriched_api_context: dict) -> ChatResponse:
    """Build direct hotel response bypassing model"""
    logger.info("[HOTEL DIRECT RESPONSE]")
    
    selected_model = request.model_variant or getattr(request, "selected_model", None) or "base"
    adapter_loaded = selected_model == "fine_tuned"
    travel_info = enriched_api_context.get("travel_info") or {}
    
    # Extract destination with fallbacks
    destination = travel_info.get("destination")
    if not destination:
        # Try to extract from message
        message_lower = request.message.lower()
        import re
        patterns = [
            r"hotel[s]?\s+(?:in\s+)?([a-z\s]+)",
            r"find\s+hotel[s]?\s+(?:in\s+)?([a-z\s]+)",
            r"looking\s+for\s+hotel[s]?\s+(?:in\s+)?([a-z\s]+)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message_lower)
            if match:
                destination = match.group(1).strip()
                break
    
    # Apply city aliases
    if destination:
        destination = CITY_ALIASES.get(destination.lower(), destination.title())
    
    logger.info("[HOTEL SEARCH] destination_present=%s", bool(destination))
    
    # If destination is still missing, return error response
    if not destination:
        return ChatResponse(
            session_id=request.session_id,
            selected_model=selected_model,
            adapter_loaded=adapter_loaded,
            parse_success=True,
            fallback_used=False,
            retry_used=False,
            assistant_message="Please provide destination city for your hotel search.",
            dashboard_payload={
                "schema_version": "travel_dashboard_v1",
                "intent": "hotel_search",
                "trip_summary": {
                    "destination": "Unknown",
                    "source": "backend_extraction"
                },
                "flight": None,
                "stay_recommendations": [],
                "weather": {"data": None, "source": "live_api", "status": "unavailable"},
                "flights": {"data": [], "source": "live_api", "status": "unavailable"},
                "hotels": {
                    "data": [],
                    "source": "live_api",
                    "status": "unavailable"
                },
                "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "map_data": {},
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": 0,
                    "food": 0,
                    "activities": 0,
                    "accommodation": 0,
                    "intercity_transport": 0,
                    "total_known_cost": 0,
                    "total": 0,
                    "remaining_budget": 0,
                    "within_budget": True,
                    "note": None,
                    "source": "empty_budget"
                },
                "dashboard_actions": ["show_hotels"],
                "api_grounding": {
                    "used_api": [],
                    "missing_api": ["hotels"],
                    "warnings": ["Destination is missing. Please provide destination city."]
                }
            }
        )
    
    # Try live hotel service first
    hotel_result = await hotel_service.search_hotels(
        destination=destination,
        check_in=travel_info.get("check_in"),
        check_out=travel_info.get("check_out"),
        guests=travel_info.get("guests", 2)
    )
    
    hotels_list = normalize_hotels_result(hotel_result)
    logger.info("[HOTEL NORMALIZED] count=%s", len(hotels_list))
    
    if hotels_list:
        # Use live hotel data
        hotel_source = "live_api"
        used_apis = ["hotels"]
        missing_apis = []
        warnings = []
        assistant_message = f"Here are available hotel options in {destination}."
    else:
        # Create static fallback hotels
        fallback_hotels = [
            {
                "name": "Hotel Europa",
                "location": destination,
                "price_per_night": "€25",
                "rating": 4.4
            },
            {
                "name": "City Circus Hotel",
                "location": destination,
                "price_per_night": "€30",
                "rating": 4.3
            },
            {
                "name": "Marriott International",
                "location": destination,
                "price_per_night": "€35",
                "rating": 4.4
            }
        ]
        
        hotels_list = fallback_hotels
        hotel_source = "static_fallback"
        used_apis = []
        missing_apis = ["hotels"]
        warnings = ["Live hotel API unavailable; showing static fallback hotel options."]
        assistant_message = f"Here are some hotel options in {destination} for your stay."
    
    logger.info("[HOTEL SOURCE] %s", hotel_source)
    
    # Build payload first, then calculate budget
    payload = {
        "schema_version": "travel_dashboard_v1",
        "intent": "hotel_search",
        "trip_summary": {
            "destination": destination,
            "source": "backend_extraction"
        },
        "flight": None,
        "stay_recommendations": [],
        "weather": {"data": None, "source": "live_api", "status": "unavailable"},
        "flights": {"data": [], "source": "live_api", "status": "unavailable"},
        "hotels": {
            "data": hotels_list,
            "source": hotel_source,
            "status": "available"
        },
        "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
        "food_recommendations": [],
        "itinerary": [],
        "map_data": {},
        "dashboard_actions": ["show_hotels"],
        "api_grounding": {
            "used_api": used_apis,
            "missing_api": missing_apis,
            "warnings": warnings
        }
    }
    
    # Calculate budget breakdown for hotel search
    payload["budget_breakdown"] = calculate_budget_breakdown(payload)
    
    return ChatResponse(
        session_id=request.session_id,
        selected_model=selected_model,
        adapter_loaded=adapter_loaded,
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message=assistant_message,
        dashboard_payload=payload,
    )


async def _build_direct_flight_response(request: ChatRequest, enriched_api_context: dict) -> ChatResponse:
    """Build direct flight response bypassing model"""
    logger.info("[FLIGHT DIRECT RESPONSE]")

    selected_model = request.model_variant or getattr(request, "selected_model", None) or "base"
    adapter_loaded = selected_model == "fine_tuned"
    travel_info = enriched_api_context.get("travel_info", {}) or {}

    message_lower = request.message.lower()
    origin_raw = None
    destination_raw = None

    logger.info(
        "[FLIGHT REQUEST PARSED] has_origin=%s has_destination=%s",
        bool(travel_info.get("origin")),
        bool(travel_info.get("destination")),
    )

    city_pattern = r"(sttugart|stuttgart|berlin|munich|münchen|heidelberg)"

    route_match = re.search(
        rf"from\s+{city_pattern}\s+to\s+{city_pattern}",
        message_lower
    )

    if route_match:
        origin_raw = route_match.group(1)
        destination_raw = route_match.group(2)
    else:
        from_match = re.search(rf"from\s+{city_pattern}", message_lower)
        to_matches = list(re.finditer(rf"\bto\s+{city_pattern}\b", message_lower))

        if from_match:
            origin_raw = from_match.group(1)

        if to_matches:
            # use last valid city after "to", not "to see"
            destination_raw = to_matches[-1].group(1)

    origin = CITY_ALIASES.get(origin_raw, origin_raw.title()) if origin_raw else None
    destination = CITY_ALIASES.get(destination_raw, destination_raw.title()) if destination_raw else None

    origin = origin or travel_info.get("origin")
    destination = destination or travel_info.get("destination")

    logger.info(
        "[FLIGHT PARAMETERS] origin_present=%s destination_present=%s",
        bool(origin),
        bool(destination),
    )

    if not destination or destination in ["Unknown", "unknown", None, ""]:
        return ChatResponse(
            session_id=request.session_id,
            selected_model=selected_model,
            adapter_loaded=adapter_loaded,
            parse_success=True,
            fallback_used=False,
            retry_used=False,
            assistant_message="Please provide destination city for your flight search.",
            dashboard_payload={
                "schema_version": "travel_dashboard_v1",
                "intent": "flight_search",
                "trip_summary": {
                    "origin": origin,
                    "destination": None,
                    "source": "backend_extraction"
                },
                "weather": {"data": None, "source": "live_api", "status": "unavailable"},
                "flights": {"data": [], "source": "live_api", "status": "unavailable"},
                "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
                "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": 0,
                    "food": 0,
                    "activities": 0,
                    "accommodation": 0,
                    "intercity_transport": 0,
                    "total_known_cost": 0,
                    "total": 0,
                    "remaining_budget": 500,
                    "remaining_budget_before_transport_and_accommodation": 500,
                    "within_budget": True,
                    "note": "Budget is estimated from available flight data.",
                    "source": "backend_budget_calculation"
                },
                "dashboard_actions": ["show_flights"],
                "api_grounding": {
                    "used_api": [],
                    "missing_api": ["flights"],
                    "warnings": ["Destination is missing. Please provide destination city."]
                }
            },
        )

    if not origin:
        origin = "Berlin"

    departure_date = normalize_flight_date(travel_info.get("departure_date"), "11/05/2026")
    return_date = normalize_flight_date(travel_info.get("return_date"), "12/05/2026")

    flights_list = []

    try:
        from backend.app.services.flight_service import FlightService
        flight_service = FlightService()

        flight_result = await flight_service.search_flights(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date
        )

        flights_list = normalize_flights_result(flight_result)
        logger.info("[CHAT FLIGHT NORMALIZED] count=%s", len(flights_list))

    except Exception as exc:
        log_operation_failure(
            logger, operation="direct_flight_search", error=exc
        )
        flights_list = []

    if flights_list:
        flights_status = "available"
        flights_source = "live_api"
        used_apis = ["flights"]
        missing_apis = []
        warnings = []
        assistant_message = f"Here are available flight options from {origin} to {destination}."
    else:
        flights_list = build_static_fallback_flights(origin, destination, departure_date, return_date)
        flights_status = "available"
        flights_source = "static_fallback"
        used_apis = []
        missing_apis = ["flights"]
        warnings = ["Live flight API unavailable; showing static fallback flight options."]
        assistant_message = f"Here are available flight options from {origin} to {destination}."

    payload = {
        "schema_version": "travel_dashboard_v1",
        "intent": "flight_search",
        "trip_summary": {
            "origin": origin,
            "destination": destination,
            "source": "backend_extraction"
        },
        "weather": {"data": None, "source": "live_api", "status": "unavailable"},
        "flights": {
            "data": flights_list,
            "source": flights_source,
            "status": flights_status
        },
        "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
        "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
        "food_recommendations": [],
        "itinerary": [],
        "dashboard_actions": ["show_flights"],
        "api_grounding": {
            "used_api": used_apis,
            "missing_api": missing_apis,
            "warnings": warnings
        }
    }

    payload["budget_breakdown"] = calculate_budget_breakdown(payload)
    transport_cost = payload["budget_breakdown"]["transport"]

    return ChatResponse(
        session_id=request.session_id,
        selected_model=selected_model,
        adapter_loaded=adapter_loaded,
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message=assistant_message,
        dashboard_payload=payload,
    )

def _build_flight_validation_response(request: ChatRequest, origin: str, destination: str) -> ChatResponse:
    """Build flight validation response when destination is missing"""
    selected_model = request.model_variant or getattr(request, "selected_model", "base")
    adapter_loaded = selected_model == "fine_tuned"
    
    return ChatResponse(
        session_id=request.session_id,
        selected_model=selected_model,
        adapter_loaded=adapter_loaded,
        parse_success=True,
        fallback_used=False,
        retry_used=False,
        assistant_message="Please provide destination city for your flight search.",
        dashboard_payload={
            "schema_version": "travel_dashboard_v1",
            "intent": "flight_search",
            "trip_summary": {
                "origin": origin,
                "destination": destination,
                "source": "backend_extraction"
            },
            "weather": {"data": None, "source": "live_api", "status": "unavailable"},
            "flights": {"data": [], "source": "live_api", "status": "unavailable"},
            "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
            "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": 0,
                "food": 0,
                "activities": 0,
                "accommodation": 0,
                "intercity_transport": 0,
                "total_known_cost": 0,
                "total": 0,
                "remaining_budget": 0,
                "within_budget": True,
                "note": None,
                "source": "empty_budget"
            },
            "dashboard_actions": ["show_flights"],
            "api_grounding": {
                "used_api": [],
                "missing_api": ["flights"],
                "warnings": ["Destination is missing. Please provide destination city."]
            }
        },
    )


async def build_static_fallback_from_context(request: ChatRequest, backend_intent: str, api_context: dict) -> dict:
    """Build static fallback response based on backend intent and API context"""
    travel_info = api_context.get("travel_info", {})
    origin = travel_info.get("origin", "Stuttgart")
    destination = travel_info.get("destination", "Heidelberg")
    duration = travel_info.get("duration_days", 3)
    
    if backend_intent == "itinerary_generation":
        # Build static fallback with preserved live API data but empty itinerary
        fallback_payload = {
            "schema_version": "travel_dashboard_v1",
            "intent": "itinerary_generation",
            "trip_summary": {
                "origin": origin,
                "destination": destination,
                "duration_days": duration,
                "budget": "budget",
                "currency": "EUR",
                "source": "backend_extraction"
            },
            "itinerary": [],  # Empty - model failed, no backend-generated activities
            "food_recommendations": [],  # Empty - model failed
            "budget_breakdown": {
                "currency": "EUR",
                "transport": 0,
                "food": 0,
                "activities": 0,
                "accommodation": 0,
                "intercity_transport": 0,
                "total_known_cost": 0,
                "total": 0,
                "remaining_budget": 0,
                "within_budget": True,
                "note": None,
                "source": "empty_budget"
            },
            "dashboard_actions": ["show_trip_summary", "show_itinerary", "show_budget"],
            "api_grounding": {
                "used_api": api_context.get("used_apis", []),
                "missing_api": [],
                "warnings": ["Model could not generate complete valid itinerary"]
            }
        }
        
        # Merge live API data into fallback
        fallback_payload = merge_live_api_context_into_dashboard(fallback_payload, api_context, backend_intent)
        
        # Ensure flights are always present for itinerary generation
        fallback_payload = await ensure_flights_for_route(
            fallback_payload,
            api_context,
            origin,
            destination
        )
        
        return {
            "assistant_message": "The model could not generate a complete non-repetitive itinerary. Please try again with fewer days or increase max_new_tokens.",
            "dashboard_payload": fallback_payload
        }
    elif backend_intent == "weather_query":
        location = "requested location"
        if "stuttgart" in request.message.lower():
            location = "Stuttgart"
        elif "heidelberg" in request.message.lower():
            location = "Heidelberg"
        
        return {
            "assistant_message": f"Here is current weather information for {location}.",
            "dashboard_payload": {
                "schema_version": "travel_dashboard_v1",
                "intent": "weather_query",
                "weather": api_context.get("weather", {"data": None, "source": "live_api", "status": "unavailable"}),
                "flights": {"data": [], "source": "live_api", "status": "unavailable"},
                "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
                "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": None,
                    "intercity_transport": None,
                    "total_known_cost": 0,
                    "note": None
                },
                "dashboard_actions": ["show_weather"],
                "api_grounding": {
                    "used_api": api_context.get("used_apis", []),
                    "missing_api": [],
                    "warnings": ["Model parsing failed, using static fallback"]
                }
            }
        }
    else:
        # Default fallback
        return {
            "assistant_message": "I encountered an error processing your request.",
            "dashboard_payload": {
                "schema_version": "travel_dashboard_v1",
                "intent": "error",
                "weather": {"data": None, "source": "live_api", "status": "unavailable"},
                "flights": {"data": [], "source": "live_api", "status": "unavailable"},
                "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
                "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
                "food_recommendations": [],
                "itinerary": [],
                "budget_breakdown": {
                    "currency": "EUR",
                    "transport": None,
                    "intercity_transport": None,
                    "total_known_cost": 0,
                    "note": None
                },
                "dashboard_actions": ["show_error"],
                "api_grounding": {
                    "used_api": [],
                    "missing_api": [],
                    "warnings": ["Model parsing failed"]
                }
            }
        }


def build_default_dashboard_payload(backend_intent: str) -> dict:
    """Build default dashboard payload for given intent"""
    if backend_intent == "weather_query":
        return {
            "schema_version": "travel_dashboard_v1",
            "intent": "weather_query",
            "weather": {"data": None, "source": "live_api", "status": "unavailable"},
            "flights": {"data": [], "source": "live_api", "status": "unavailable"},
            "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
            "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "dashboard_actions": ["show_weather"],
            "api_grounding": {
                "used_api": [],
                "missing_api": [],
                "warnings": []
            }
        }
    elif backend_intent == "itinerary_generation":
        return {
            "schema_version": "travel_dashboard_v1",
            "intent": "itinerary_generation",
            "trip_summary": {},
            "weather": {"data": None, "source": "live_api", "status": "unavailable"},
            "flights": {"data": [], "source": "live_api", "status": "unavailable"},
            "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
            "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "dashboard_actions": ["show_trip_summary", "show_itinerary", "show_budget"],
            "api_grounding": {
                "used_api": [],
                "missing_api": [],
                "warnings": []
            }
        }
    else:
        return {
            "schema_version": "travel_dashboard_v1",
            "intent": "error",
            "weather": {"data": None, "source": "live_api", "status": "unavailable"},
            "flights": {"data": [], "source": "live_api", "status": "unavailable"},
            "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
            "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "dashboard_actions": ["show_error"],
            "api_grounding": {
                "used_api": [],
                "missing_api": [],
                "warnings": []
            }
        }


def merge_live_api_context_into_dashboard(payload: dict, api_context: dict, backend_intent: str) -> dict:
    """Merge live API context into dashboard payload"""
    # Weather merge
    weather_data = api_context.get("weather") or api_context.get("weather_data")
    if weather_data:
        payload["weather"] = {
            "data": weather_data,
            "source": "live_api",
            "status": "available"
        }
    else:
        payload["weather"] = {
            "data": None,
            "source": "live_api",
            "status": "unavailable"
        }
    
    # Flights merge
    flights_data = api_context.get("flights") or api_context.get("flight_data")
    if flights_data:
        payload["flights"] = {
            "data": flights_data,
            "source": api_context.get("flight_source", "live_api"),
            "status": "available"
        }
    else:
        payload["flights"] = {
            "data": [],
            "source": "live_api",
            "status": "unavailable"
        }
    
    # Hotels merge
    hotels_data = api_context.get("hotels") or api_context.get("hotel_data")
    if hotels_data:
        payload["hotels"] = {
            "data": hotels_data,
            "source": api_context.get("hotel_source", "live_api"),
            "status": "available"
        }
        payload["stay_recommendations"] = _as_model_list(hotels_data)
    else:
        existing_hotels = payload.get("hotels") if isinstance(payload.get("hotels"), dict) else {}
        existing_hotel_data = _as_model_list(existing_hotels.get("data"))
        existing_stays = _as_model_list(payload.get("stay_recommendations"))
        model_hotel_data = existing_hotel_data or existing_stays
        if model_hotel_data:
            payload["hotels"] = {
                "data": model_hotel_data,
                "source": existing_hotels.get("source") or "model_generated",
                "status": "available",
            }
            payload["stay_recommendations"] = model_hotel_data
        else:
            payload["hotels"] = {
                "data": [],
                "source": "live_api",
                "status": "unavailable"
            }
            payload["stay_recommendations"] = []
    
    # Events merge
    events_data = api_context.get("local_events") or api_context.get("events") or api_context.get("events_data")
    if events_data:
        payload["local_events"] = {
            "data": events_data,
            "source": "live_api",
            "status": "available"
        }
    else:
        payload["local_events"] = {
            "data": [],
            "source": "live_api",
            "status": "unavailable"
        }
    
    payload["map_data"] = _normalize_map_data(payload)
    return payload


def detect_user_intent(message: str) -> str:
    """
    Detect user intent from message with priority order
    Backend intent must override model-generated intent
    """
    message_lower = message.lower()
    
    # Itinerary keywords (highest priority)
    itinerary_keywords = [
        "plan", "itinerary", "trip", "day", "days", "budget trip", "travel plan", 
        "schedule", "route", "vacation", "weekend trip"
    ]
    
    # Weather keywords
    weather_keywords = [
        "weather", "temperature", "forecast", "rain", "sunny", "cloudy"
    ]
    
    # Flight keywords (explicit only)
    flight_keywords = [
        "flight", "flights", "fly", "airline", "airport", "airfare", "ticket", "plane"
    ]
    
    # Hotel keywords
    hotel_keywords = [
        "hotel", "hotels", "stay", "accommodation", "room"
    ]
    
    # Events keywords
    events_keywords = [
        "event", "activity", "things to do", "attraction", "museum", "concert",
        "festival", "show", "entertainment", "tour", "sightseeing"
    ]
    
    # Priority: itinerary > weather > flight > hotel > events
    if any(keyword in message_lower for keyword in itinerary_keywords):
        return "itinerary_generation"
    elif any(keyword in message_lower for keyword in weather_keywords):
        return "weather_query"
    elif any(keyword in message_lower for keyword in flight_keywords):
        return "flight_search"
    elif any(keyword in message_lower for keyword in hotel_keywords):
        return "hotel_search"
    elif any(keyword in message_lower for keyword in events_keywords):
        return "events_search"
    else:
        # Default fallback
        return "itinerary_generation"


def enforce_intent_specific_dashboard(payload: dict) -> dict:
    """
    Enforce strict intent-based dashboard payload cleanup
    Removes all irrelevant fields based on detected intent
    Always returns a valid payload with full schema sections
    """
    intent = payload.get("intent", "")
    
    # Safety check: ensure payload is not None
    if not payload or not isinstance(payload, dict):
        return payload
    
    # Base structure with all required fields
    base_payload = {
        "schema_version": payload.get("schema_version", "travel_dashboard_v1"),
        "intent": intent,
        "trip_summary": payload.get("trip_summary", {}),
        "flight": payload.get("flight", None),
        "stay_recommendations": payload.get("stay_recommendations", []),
        "weather": payload.get("weather", None),
        "flights": payload.get("flights", []),
        "hotels": payload.get("hotels", []),
        "local_events": payload.get("local_events", []),
        "food_recommendations": payload.get("food_recommendations", []),
        "itinerary": payload.get("itinerary", []),
        "map_data": payload.get("map_data", {}),
        "budget_breakdown": payload.get("budget_breakdown", {
            "currency": "EUR",
            "transport": None,
            "intercity_transport": None,
            "total_known_cost": 0,
            "note": None
        }),
        "dashboard_actions": payload.get("dashboard_actions", []),
        "api_grounding": payload.get("api_grounding", {
            "used_api": [],
            "missing_api": [],
            "warnings": []
        })
    }
    
    if intent == "weather_query":
        # Weather-only response: keep only weather data
        used_apis = []
        if payload.get("weather") and payload.get("weather", {}).get("data"):
            used_apis.append("weather")
        
        return {
            **base_payload,
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "map_data": None,
            "dashboard_actions": ["show_weather"],
            "api_grounding": {
                "used_api": used_apis,
                "missing_api": [],
                "warnings": []
            }
        }
    elif intent == "flight_search":
        # Flight-only response: keep only flights data
        return {
            **base_payload,
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "map_data": None,
            "dashboard_actions": ["show_flights"],
            "api_grounding": {
                "used_api": ["flights"],
                "missing_api": [],
                "warnings": []
            }
        }
    elif intent == "hotel_search":
        # Hotel-only response: keep only hotels data
        return {
            **base_payload,
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "map_data": None,
            "dashboard_actions": ["show_hotels"],
            "api_grounding": {
                "used_api": ["hotels"],
                "missing_api": [],
                "warnings": []
            }
        }
    elif intent == "events_search":
        # Events-only response: keep only events data
        return {
            **base_payload,
            "food_recommendations": [],
            "itinerary": [],
            "budget_breakdown": {
                "currency": "EUR",
                "transport": None,
                "intercity_transport": None,
                "total_known_cost": 0,
                "note": None
            },
            "map_data": None,
            "dashboard_actions": ["show_events"],
            "api_grounding": {
                "used_api": ["events"],
                "missing_api": [],
                "warnings": []
            }
        }
    elif intent == "itinerary_generation":
        # Itinerary generation: keep trip, itinerary, budget + available APIs
        used_apis = []
        if payload.get("weather") and payload.get("weather", {}).get("data"):
            used_apis.append("weather")
        if payload.get("flights") and payload.get("flights", {}).get("data"):
            used_apis.append("flights")
        if payload.get("hotels") and payload.get("hotels", {}).get("data"):
            used_apis.append("hotels")
        if payload.get("local_events") and payload.get("local_events", {}).get("data"):
            used_apis.append("events")
        
        return {
            **base_payload,
            "dashboard_actions": ["show_trip_summary", "show_itinerary", "show_budget"],
            "api_grounding": {
                "used_api": used_apis,
                "missing_api": [],
                "warnings": []
            }
        }
    else:
        # Default/error: keep full structure
        return base_payload


def _get_model(variant: ModelVariant, config: AdapterConfig):
    global _loaded_model

    with _model_lock:
        if _loaded_model and _loaded_model.variant == variant:
            logger.info("Reusing already-loaded model variant=%s", variant)
            return _loaded_model.tokenizer, _loaded_model.model

        _unload_current_model()
        if variant == "base":
            logger.info("Loading model variant=base")
            tokenizer, model = load_base_model_only(config)
        else:
            logger.info("Loading model variant=fine_tuned")
            tokenizer, model = load_base_model_and_adapter(config)

        _loaded_model = LoadedModel(variant=variant, tokenizer=tokenizer, model=model)
        logger.info("Loaded model variant=%s", variant)
        return tokenizer, model


def _unload_current_model() -> None:
    global _loaded_model

    if _loaded_model is None:
        return
    logger.info("Unloading model variant=%s", _loaded_model.variant)
    del _loaded_model.model
    del _loaded_model.tokenizer
    _loaded_model = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _detect_max_itinerary(message: str) -> int:
    """Return the max number of itinerary items based on trip duration in the message."""
    m = re.search(r"(\d+)[- ]day", message, re.IGNORECASE)
    if m:
        days = int(m.group(1))
        if days <= 1:
            return 3
        if days <= 3:
            return 6
        if days <= 5:
            return 8
        return 10
    return 6


def _format_api_context_compact(api_context: dict[str, Any], include_event_details: bool = True) -> str:
    """Serialize api_context as a short human-readable string to save tokens."""
    parts: list[str] = []
    for key in ("flights", "hotels"):
        val = api_context.get(key)
        parts.append(f"{key}: {'unavailable' if not val else json.dumps(val, ensure_ascii=False)}")
    local_events = api_context.get("local_events")
    if include_event_details:
        parts.append(
            f"local_events: {'unavailable' if not local_events else json.dumps(local_events, ensure_ascii=False)}"
        )
    else:
        event_count = len(local_events) if isinstance(local_events, list) else 0
        parts.append(f"local_events: {'unavailable' if not event_count else str(event_count) + ' available; backend injects separately'}")
    weather = api_context.get("weather")
    parts.append(f"weather: {'unavailable' if weather is None else json.dumps(weather, ensure_ascii=False)}")
    return " | ".join(parts)


def _build_prompt(message: str, api_context: dict[str, Any], backend_intent: str) -> str:
    max_items = _detect_max_itinerary(message)
    api_summary = _format_api_context_compact(
        api_context,
        include_event_details=backend_intent != "itinerary_generation",
    )
    
    # Detect which services are actually available
    has_weather = bool(api_context.get("weather"))
    has_flights = bool(api_context.get("flights"))
    has_hotels = bool(api_context.get("hotels"))
    has_events = bool(api_context.get("local_events"))
    
    # Build intent-aware prompt based on backend intent and available services
    if backend_intent == "weather_query":
        # Weather-only request
        return (
            "You are a weather API response generator. Output ONLY weather data. No other fields.\n"
            f"USER: {message}\n"
            f"DETECTED INTENT: {backend_intent}\n"
            f"LIVE API CONTEXT: {api_summary}\n"
            "IMPORTANT: Use the provided live_api_context. Do not say no live API data was provided if api_context contains data.\n"
            "You must output EXACTLY this JSON structure:\n"
            '{"assistant_message":"Here is the current weather information for your requested location.",'
            '"dashboard_payload":{'
            '"schema_version":"travel_dashboard_v1",'
            f'"intent":"{backend_intent}",'
            '"weather":{...weather_data...},'
            '"flights":{"data":[],"source":"live_api","status":"unavailable"},'
            '"hotels":{"data":[],"source":"live_api","status":"unavailable"},'
            '"local_events":{"data":[],"source":"live_api","status":"unavailable"},'
            '"trip_summary":null,'
            '"food_recommendations":null,'
            '"itinerary":null,'
            '"map_data":null,'
            '"budget_breakdown":null,'
            '"dashboard_actions":["show_weather"],'
            '"api_grounding":{"used_api":["weather"],"missing_api":[],"warnings":[]}'
            "}}\n"
            f"STRICT RULES: No itinerary, no food recommendations, no trip summary, no budget breakdown. ONLY weather data.\n"
            f"Weather data available: {api_summary}\n"
        )
    
    elif has_flights and not has_weather and not has_hotels and not has_events:
        # Flight-only request
        return (
            "You are a flight API response generator. Output ONLY flight data. No other fields.\n"
            f"USER: {message}\n"
            f"APIs: {api_summary}\n"
            "You must output EXACTLY this JSON structure:\n"
            '{"assistant_message":"Here are the available flight options for your requested route.",'
            '"dashboard_payload":{'
            '"schema_version":"travel_dashboard_v1",'
            '"intent":"flight_search",'
            '"weather":{"data":null,"source":"live_api","status":"unavailable"},'
            '"flights":{...flight_data...},'
            '"hotels":{"data":[],"source":"live_api","status":"unavailable"},'
            '"local_events":{"data":[],"source":"live_api","status":"unavailable"},'
            '"trip_summary":null,'
            '"food_recommendations":null,'
            '"itinerary":null,'
            '"map_data":null,'
            '"budget_breakdown":null,'
            '"dashboard_actions":["show_flights"],'
            '"api_grounding":{"used_api":["flights"],"missing_api":[],"warnings":[]}'
            "}}\n"
            f"STRICT RULES: No weather, no hotels, no events, no food recommendations, no trip summary. ONLY flight data.\n"
            f"Flight data available: {api_summary}\n"
        )
    
    elif has_hotels and not has_weather and not has_flights and not has_events:
        # Hotel-only request
        return (
            "You are a hotel API response generator. Output ONLY hotel data. No other fields.\n"
            f"USER: {message}\n"
            f"APIs: {api_summary}\n"
            "You must output EXACTLY this JSON structure:\n"
            '{"assistant_message":"Here are the available hotel options for your requested destination.",'
            '"dashboard_payload":{'
            '"schema_version":"travel_dashboard_v1",'
            '"intent":"hotel_search",'
            '"weather":{"data":null,"source":"live_api","status":"unavailable"},'
            '"flights":{"data":[],"source":"live_api","status":"unavailable"},'
            '"hotels":{...hotel_data...},'
            '"local_events":{"data":[],"source":"live_api","status":"unavailable"},'
            '"trip_summary":null,'
            '"food_recommendations":null,'
            '"itinerary":null,'
            '"map_data":null,'
            '"budget_breakdown":null,'
            '"dashboard_actions":["show_hotels"],'
            '"api_grounding":{"used_api":["hotels"],"missing_api":[],"warnings":[]}'
            "}}\n"
            f"STRICT RULES: No weather, no flights, no events, no food recommendations, no trip summary. ONLY hotel data.\n"
            f"Hotel data available: {api_summary}\n"
        )
    
    elif backend_intent == "itinerary_generation":
        travel_info = api_context.get("travel_info", {})
        duration_days = travel_info.get("duration_days", 3)
        origin = travel_info.get("origin", "Origin")
        destination = travel_info.get("destination", "Destination")
        
        return (
            "Return one compact JSON object only; no markdown or explanation.\n"
            'Use exactly two root keys: "itinerary" and "food_recommendations".\n'
            f"Create {duration_days} days for {origin} to {destination}; exactly Morning, Afternoon, and Evening per day.\n"
            "Use short, unique activities. Prefer landmarks, walks, neighborhoods, culture, and low-cost options.\n"
            "Each itinerary item: day, time, activity, budget_eur.\n"
            "Food: at most 3 concise options with name, price_range, and type.\n"
            "Do not output trip summary, weather, flights, hotels, events, maps, budgets, actions, or API metadata; backend adds them.\n"
            f"User request: {message}\n"
            'Shape: {"itinerary":[{"day":1,"time":"Morning","activity":"...","budget_eur":0}],'
            '"food_recommendations":[{"name":"...","price_range":"low","type":"food"}]}'
        )
    else:
        # Default fallback for other intents
        return (
            "You are a travel assistant. Output a basic response.\n"
            f"USER: {message}\n"
            f"DETECTED INTENT: {backend_intent}\n"
            f"LIVE API CONTEXT: {api_summary}\n"
            "IMPORTANT: Use the provided live_api_context. Do not say no live API data was provided if api_context contains data.\n"
            '{"assistant_message":"Basic travel response.",'
            '"dashboard_payload":{'
            f'"intent":"{backend_intent}",'
            '"weather":{...weather_data_if_available...},'
            '"flights":{...flight_data_if_available...},'
            '"hotels":{...hotel_data_if_available...},'
            '"local_events":{...event_data_if_available...},'
            '"food_recommendations":[],'
            '"itinerary":[],'
            '"budget_breakdown":{"currency":"EUR","transport":null,"food":null,"activities":null,"total":0},'
            '"dashboard_actions":["show_error"],'
            '"api_grounding":{"used_api":[...successful_apis...],"missing_api":[],"warnings":[]}'
            "}}\n"
        )


