"""Parse and normalize Wanderly dashboard JSON from model text."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Optional, Dict, List

from pydantic import BaseModel, ConfigDict, Field


API_ALIASES: Dict[str, str] = {
    "flight": "flights",
    "flight_api": "flights",
    "flights_api": "flights",
    "hotel": "hotels",
    "hotel_api": "hotels",
    "hotels_api": "hotels",
    "stay": "hotels",
    "stay_api": "hotels",
    "stays": "hotels",
    "weather_api": "weather",
    "weather_data": "weather",
    "event": "local_events",
    "events": "local_events",
    "event_api": "local_events",
    "local_event": "local_events",
    "local_events": "local_events",
    "destination_context": "destination",
    "destination_api": "destination",
    "city_api": "destination",
    "attraction_api": "destination",
    "food_api": "destination",
}

MISSING_API_ALIASES: Dict[str, str] = {
    **API_ALIASES,
    "event": "events",
    "events": "events",
    "event_api": "events",
    # Note: stay_recommendations is excluded to prevent it from being treated as a missing API
    "local_event": "events",
    "local_events": "events",
}

DEFAULT_DASHBOARD_PAYLOAD: Dict[str, Any] = {
    "schema_version": "travel_dashboard_v1",
    "intent": "itinerary_generation",
    "trip_summary": {},
    "flight": None,
    "stay_recommendations": [],
    "food_recommendations": [],
    "itinerary": [],
    "map_data": {"markers": [], "route_segments": []},
    "budget_breakdown": {},
    "dashboard_actions": [],
    "weather": None,
    "local_events": [],
    "api_grounding": {"used_api": [], "missing_api": [], "warnings": []},
}


class ApiGroundingPayload(BaseModel):
    used_api: List[str] = Field(default_factory=list)
    missing_api: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DashboardPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    intent: str
    trip_summary: Dict[str, Any]
    flight: Optional[Any] = None
    stay_recommendations: List[Any] = Field(default_factory=list)
    weather: Any = None
    flights: Any = Field(default_factory=list)
    hotels: Any = Field(default_factory=list)
    local_events: Any = Field(default_factory=list)
    food_recommendations: List[Any]
    itinerary: List[Any]
    map_data: Dict[str, Any]
    budget_breakdown: Dict[str, Any]
    dashboard_actions: List[str]
    api_grounding: ApiGroundingPayload


class ModelDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    assistant_message: str
    dashboard_payload: DashboardPayload


def extract_json_object(text: str) -> dict[str, Any]:
    """Load the first balanced JSON object from raw model output."""
    stripped = text.strip()
    payload = _loads_json_candidate(stripped)
    if isinstance(payload, dict):
        return payload

    start = stripped.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(stripped[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                payload = _loads_json_candidate(stripped[start : index + 1])
                if isinstance(payload, dict):
                    return payload
                raise ValueError("root must be a JSON object")

    raise ValueError("no complete JSON object found in model output")


_STRING_VALUE_KEYS = frozenset(
    {
        "area", "location", "destination", "departure_city", "city", "country",
        "travel_style", "traveler_type", "currency", "name", "price_range",
        "activity", "time", "status", "reason", "season", "travelers", "budget",
        "transport", "food", "activities", "total",
    }
)
_RESERVED_JSON_WORDS = frozenset({"true", "false", "null"})


def _loads_json_candidate(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_common_model_json(candidate)
        if repaired == candidate:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _repair_common_model_json(candidate: str) -> str:
    """Repair narrow, common model typos without accepting arbitrary JSON-like text."""
    repaired = candidate
    repaired = re.sub(r'"([A-Za-z_][A-Za-z0-9_ ]*):\s+"', r'"\1": "', repaired)
    repaired = re.sub(
        r'"([A-Za-z_][A-Za-z0-9_]*)\s+(?=[\[{])',
        r'"\1":',
        repaired,
    )
    repaired = re.sub(
        r'"([A-Za-z_][A-Za-z0-9_]*)"\s+(?=[\[{])',
        r'"\1":',
        repaired,
    )
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = _quote_unquoted_string_values(repaired)
    repaired = _close_unbalanced_json_object(repaired)
    return repaired


def _quote_unquoted_string_values(candidate: str) -> str:
    """Quote bare word values for known string-typed keys.

    Handles: "key": Word  →  "key": "Word"
    Only matches known safe keys; skips numbers, booleans, null, and already-quoted values.
    """
    keys_pattern = "|".join(re.escape(k) for k in sorted(_STRING_VALUE_KEYS, key=len, reverse=True))
    pattern = re.compile(
        r'("(?:' + keys_pattern + r')")\s*:\s*'
        r'(?!"|\[|\{|true\b|false\b|null\b|-?\d)'
        r'([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 _\-\'\.]*)',
    )

    def _quote_match(m: re.Match) -> str:
        key_part = m.group(1)
        value = m.group(2).rstrip()
        if value.lower() in _RESERVED_JSON_WORDS:
            return m.group(0)
        return f'{key_part}: "{value}"'

    return pattern.sub(_quote_match, candidate)


def _close_unbalanced_json_object(candidate: str) -> str:
    """Append missing closing braces/brackets if the JSON object is cut off."""
    stripped = candidate.rstrip()
    if not stripped.startswith("{"):
        return candidate

    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escaped = False

    for char in stripped:
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth_brace += 1
        elif char == "}":
            depth_brace -= 1
        elif char == "[":
            depth_bracket += 1
        elif char == "]":
            depth_bracket -= 1

    suffix = "]" * max(depth_bracket, 0) + "}" * max(depth_brace, 0)
    if suffix:
        # Remove trailing comma before we close
        trimmed = re.sub(r",\s*$", "", stripped)
        return trimmed + suffix
    return candidate


def safe_parse_and_normalize(
    text: str,
    api_context: dict[str, Any] | None = None,
    user_message: str = "",
) -> dict[str, Any]:
    """Parse raw model output and normalize it for schema validation."""
    return validate_normalized_response(
        normalize_dashboard_payload(extract_json_object(text), api_context, user_message)
    )


def normalize_dashboard_payload(
    payload: dict[str, Any],
    api_context: dict[str, Any] | None = None,
    user_message: str = "",
) -> dict[str, Any]:
    normalized = deepcopy(payload)
    assistant_message = normalized.get("assistant_message")
    if not isinstance(assistant_message, str) or not assistant_message.strip():
        normalized["assistant_message"] = "Here is the normalized travel dashboard payload."

    dashboard_payload = normalized.get("dashboard_payload")
    if not isinstance(dashboard_payload, dict):
        dashboard_payload = {}
    dashboard_payload = _with_dashboard_defaults(dashboard_payload)
    _coerce_field_types(dashboard_payload)
    _sanitize_trip_summary(dashboard_payload, user_message)
    _normalize_dashboard_aliases(dashboard_payload)
    _normalize_api_grounding(dashboard_payload)
    _deduplicate_itinerary(dashboard_payload)
    _filter_non_food_recommendations(dashboard_payload, api_context)
    _strip_root_api_fields(dashboard_payload)
    # Anti-hallucination sanitization (Rules 1–8, 11)
    _sanitize_food_recommendations(dashboard_payload, api_context, user_message)
    _sanitize_itinerary_costs(dashboard_payload, api_context)
    _ensure_minimum_itinerary(dashboard_payload)
    _sanitize_budget_breakdown(dashboard_payload, api_context, user_message)
    _strip_invalid_exchange_rate(dashboard_payload, api_context)
    _ensure_dashboard_actions(dashboard_payload)

    normalized["dashboard_payload"] = dashboard_payload
    return normalized


def _has_live_data(value: Any) -> bool:
    """Check if API data contains real live data."""
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return bool(value)


def enforce_api_context_truth(
    payload: dict[str, Any],
    api_context: dict[str, Any] | None,
    user_message: str = "",
) -> dict[str, Any]:
    """Force API-backed fields to match API context instead of model guesses."""
    normalized = normalize_dashboard_payload(payload, api_context, user_message)
    dashboard_payload = normalized["dashboard_payload"]
    context = api_context if isinstance(api_context, dict) else {}

    flights = _as_list(context.get("flights"))
    hotels = _as_list(context.get("hotels"))
    weather = context.get("weather")
    local_events = _as_list(context.get("local_events"))
    weather_source = context.get("weather_source", "provided_context" if weather else "live_api")
    flight_source = context.get("flight_source", "provided_context" if flights else "live_api")
    hotel_source = context.get("hotel_source", "provided_context" if hotels else "live_api")
    event_source = context.get("event_source", "provided_context" if local_events else "live_api")

    # Check for missing API keys in warnings
    warnings = context.get("warnings", [])
    weather_missing_key = "Weather service unavailable - API key missing" in warnings
    flights_missing_key = "FLIGHT_API_KEY not found" in warnings or "Flight service unavailable - API key missing" in warnings
    hotels_missing_key = "RAPIDAPI_KEY not found" in warnings or "Hotel service unavailable - API key missing" in warnings
    events_missing_key = "TICKETMASTER_API_KEY not found" in warnings or "Events service unavailable - API key missing" in warnings

    # Add provenance structure for API data
    dashboard_payload["weather"] = {
        "data": weather,
        "source": weather_source,
        "status": "missing_api_key" if weather_missing_key else ("available" if _has_live_data(weather) else "unavailable")
    }
    dashboard_payload["flights"] = {
        "data": flights,
        "source": flight_source,
        "status": "missing_api_key" if flights_missing_key else ("available" if _has_live_data(flights) else "unavailable")
    }
    dashboard_payload["hotels"] = {
        "data": hotels,
        "source": hotel_source,
        "status": "missing_api_key" if hotels_missing_key else ("available" if _has_live_data(hotels) else "unavailable")
    }
    dashboard_payload["local_events"] = {
        "data": local_events,
        "source": event_source,
        "status": "missing_api_key" if events_missing_key else ("available" if _has_live_data(local_events) else "unavailable")
    }

    # Add provenance for trip summary (backend extracted)
    travel_info = context.get("travel_info", {})
    if "trip_summary" in dashboard_payload:
        dashboard_payload["trip_summary"]["source"] = "backend_extraction"
        # Update with extracted info if missing
        dashboard_payload["trip_summary"].update({
            "destination": dashboard_payload["trip_summary"].get("destination", travel_info.get("destination", "Unknown")),
            "duration_days": dashboard_payload["trip_summary"].get("duration_days", travel_info.get("duration_days", 2)),
            "origin": dashboard_payload["trip_summary"].get("origin", travel_info.get("origin", "Berlin")),
            "budget": dashboard_payload["trip_summary"].get("budget", travel_info.get("budget", "budget")),
            "currency": "EUR",  # Standardize currency for European context
        })

    # Add schema version for frontend compatibility
    dashboard_payload["schema_version"] = "travel_dashboard_v1"

    # Add provenance for model-generated content
    dashboard_payload["itinerary_source"] = "model_generated"
    dashboard_payload["assistant_message_source"] = "model_generated"  # Will be overridden in ai_model_service based on model variant

    # Update API grounding with canonical names only
    api_grounding = dashboard_payload.setdefault("api_grounding", {"used_api": [], "missing_api": [], "warnings": []})
    
    # Update used APIs
    if weather is not None and weather_source == "live_api":
        if "weather" not in api_grounding["used_api"]:
            api_grounding["used_api"].append("weather")
    else:
        if "weather" not in api_grounding["missing_api"]:
            api_grounding["missing_api"].append("weather")
    
    if flights and flight_source == "live_api":
        if "flights" not in api_grounding["used_api"]:
            api_grounding["used_api"].append("flights")
    else:
        if "flights" not in api_grounding["missing_api"]:
            api_grounding["missing_api"].append("flights")
    
    if hotels and hotel_source == "live_api":
        if "hotels" not in api_grounding["used_api"]:
            api_grounding["used_api"].append("hotels")
    else:
        if "hotels" not in api_grounding["missing_api"]:
            api_grounding["missing_api"].append("hotels")
    
    if local_events and event_source == "live_api":
        if "events" not in api_grounding["used_api"]:
            api_grounding["used_api"].append("events")
    else:
        if "events" not in api_grounding["missing_api"]:
            api_grounding["missing_api"].append("events")

    # Final normalization and warnings
    _normalize_api_grounding(dashboard_payload)
    _add_static_grounding_warning(dashboard_payload)

    # Rule 9: clean up fake data claims in assistant_message
    _sanitize_assistant_message(normalized, api_context)

    # Fix 8: deduplicate and normalize warnings before returning
    _dedupe_warnings(dashboard_payload)

    return validate_normalized_response(normalized)


def build_safe_fallback_response(warning: Optional[str] = None) -> Dict[str, Any]:
    # Build canonical fallback response
    dashboard_payload = {
        "schema_version": "travel_dashboard_v1",
        "intent": "clarification_needed",
        "trip_summary": {
            "destination": "Unknown",
            "origin": "Berlin", 
            "duration_days": 2,
            "travelers": "solo",
            "budget": "budget",
            "currency": "EUR",
            "source": "backend_extraction"
        },
        "weather": {"data": None, "source": "live_api", "status": "unavailable"},
        "flights": {"data": [], "source": "live_api", "status": "unavailable"},
        "hotels": {"data": [], "source": "live_api", "status": "unavailable"},
        "local_events": {"data": [], "source": "live_api", "status": "unavailable"},
        "food_recommendations": [],
        "itinerary": [],
        "budget_breakdown": {
            "transport": "mock",
            "food": "mock", 
            "activities": "mock",
            "total": "mock"
        },
        "dashboard_actions": ["ask_for_missing_trip_details"],
        "itinerary_source": "model_generated",
        "assistant_message_source": "model_generated",
        "api_grounding": {
            "used_api": [],
            "missing_api": ["weather", "flights", "hotels", "events"],
            "warnings": [warning or "Model output could not be safely parsed."],
        }
    }
    
    return {
        "assistant_message": (
            "I could not safely generate a complete structured trip plan. "
            "Please try again with destination, dates, budget, and preferences."
        ),
        "dashboard_payload": dashboard_payload
    }


def validate_normalized_response(payload: dict[str, Any]) -> dict[str, Any]:
    # Normalize live API sections to canonical structure before validation
    dashboard_payload = payload.get("dashboard_payload", {})
    
    # Normalize weather, flights, hotels, local_events to canonical structure
    # Handle both canonical fields and legacy fields for backward compatibility
    weather_data = (
        dashboard_payload.get("weather") or 
        dashboard_payload.get("weather_data")
    )
    flights_data = (
        dashboard_payload.get("flights") or 
        dashboard_payload.get("flight_options") or 
        dashboard_payload.get("flight")
    )
    hotels_data = (
        dashboard_payload.get("hotels") or 
        dashboard_payload.get("hotel_options") or 
        dashboard_payload.get("stay_recommendations")
    )
    local_events_data = (
        dashboard_payload.get("local_events") or 
        dashboard_payload.get("events")
    )
    
    # Create canonical structure
    dashboard_payload["weather"] = _normalize_live_api_section(weather_data, None)
    dashboard_payload["flights"] = _normalize_live_api_section(flights_data, [])
    dashboard_payload["hotels"] = _normalize_live_api_section(hotels_data, [])
    dashboard_payload["local_events"] = _normalize_live_api_section(local_events_data, [])
    
    # Remove legacy duplicate fields
    _remove_legacy_duplicate_fields(dashboard_payload)
    
    # Compute API grounding from canonical fields (not from model output)
    api_grounding = dashboard_payload.setdefault("api_grounding", {"used_api": [], "missing_api": [], "warnings": []})
    
    # Get canonical API sections
    canonical_apis = {
        "weather": dashboard_payload.get("weather", {}),
        "flights": dashboard_payload.get("flights", {}),
        "hotels": dashboard_payload.get("hotels", {}),
        "events": dashboard_payload.get("local_events", {})
    }
    
    # Compute used and missing APIs based on actual data presence
    used_api = []
    missing_api = []
    
    for api_name, section in canonical_apis.items():
        if isinstance(section, dict):
            status = section.get("status", "unavailable")
            data = section.get("data")
            if status == "available" and _has_live_data(data):
                used_api.append(api_name)
            else:
                missing_api.append(api_name)
        else:
            # For non-dict sections, treat as missing
            missing_api.append(api_name)
    
    # Update API grounding with canonical names only
    api_grounding["used_api"] = used_api
    api_grounding["missing_api"] = missing_api
    
    # Add static food fallback if food_recommendations is empty after filtering
    food_recs = dashboard_payload.get("food_recommendations", [])
    if not food_recs and isinstance(food_recs, list):
        # Add 2-3 safe static food suggestions for common destinations
        static_foods = [
            {
                "name": "Local Bakery",
                "price_range": "low",
                "type": "food",
                "source": "static_food_fallback"
            },
            {
                "name": "Traditional Café",
                "price_range": "low", 
                "type": "food",
                "source": "static_food_fallback"
            },
            {
                "name": "Market Square Food Stalls",
                "price_range": "low",
                "type": "food",
                "source": "static_food_fallback"
            }
        ]
        dashboard_payload["food_recommendations"] = static_foods
    
    # Validate with Pydantic model
    validated = ModelDashboardResponse.model_validate({
        "assistant_message": payload.get("assistant_message", ""),
        "dashboard_payload": dashboard_payload
    })
    
    # Convert to dict for final cleanup
    final_payload = validated.model_dump(mode="python")
    final_dashboard = final_payload["dashboard_payload"]
    
    # Remove legacy fields from final response
    for legacy_key in [
        "flight",
        "stay_recommendations", 
        "flight_options",
        "hotel_options",
        "weather_data",
        "events",
    ]:
        final_dashboard.pop(legacy_key, None)
    
    # Clean up API grounding warnings - remove incorrect warning when APIs are available
    api_grounding = final_dashboard.get("api_grounding", {})
    used_api = api_grounding.get("used_api", [])
    missing_api = api_grounding.get("missing_api", [])
    warnings = api_grounding.get("warnings", [])
    
    # Remove warning about missing live data when it's actually available
    cleaned_warnings = []
    for warning in warnings:
        if ("No live transport, hotel, weather, event, or place-rating API data was provided" in warning and
            ("weather" in used_api or "flights" in used_api or "hotels" in used_api)):
            continue  # Skip this incorrect warning
        cleaned_warnings.append(warning)
    
    # Rebuild API grounding with canonical names only
    final_dashboard["api_grounding"] = {
        "used_api": used_api,
        "missing_api": missing_api,
        "warnings": cleaned_warnings
    }
    
    return final_payload


def _with_dashboard_defaults(dashboard_payload: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_DASHBOARD_PAYLOAD)
    for key, value in dashboard_payload.items():
        merged[key] = value
    merged["schema_version"] = "travel_dashboard_v1"

    if not isinstance(merged.get("map_data"), dict):
        merged["map_data"] = {"markers": [], "route_segments": []}
    else:
        merged["map_data"].setdefault("markers", [])
        merged["map_data"].setdefault("route_segments", [])

    return merged


def _coerce_field_types(dashboard_payload: dict[str, Any]) -> None:
    """Coerce common model type errors so validation doesn't fall back unnecessarily.

    Models frequently output lists/dicts as JSON-encoded strings or swap list↔dict.
    This function fixes those shapes in-place before Pydantic validation runs.
    """
    # trip_summary must be dict
    ts = dashboard_payload.get("trip_summary")
    if isinstance(ts, str):
        dashboard_payload["trip_summary"] = {"summary": ts} if ts.strip() else {}
    elif not isinstance(ts, dict):
        dashboard_payload["trip_summary"] = {}

    # budget_breakdown must be dict
    bb = dashboard_payload.get("budget_breakdown")
    if isinstance(bb, str):
        try:
            parsed = json.loads(bb)
            dashboard_payload["budget_breakdown"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            dashboard_payload["budget_breakdown"] = {}
    elif not isinstance(bb, dict):
        dashboard_payload["budget_breakdown"] = {}

    # List fields: accept str-encoded JSON, a bare dict (wrap), or None
    _LIST_FIELDS = (
        "food_recommendations",
        "itinerary",
        "local_events",
        "dashboard_actions",
        "stay_recommendations",
    )
    for field in _LIST_FIELDS:
        val = dashboard_payload.get(field)
        if val is None:
            dashboard_payload[field] = []
        elif isinstance(val, str):
            stripped = val.strip()
            if not stripped or stripped in ("[]", "null"):
                dashboard_payload[field] = []
            else:
                try:
                    parsed = json.loads(stripped)
                    dashboard_payload[field] = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
                except Exception:
                    dashboard_payload[field] = [stripped]
        elif isinstance(val, dict):
            # Model returned a dict instead of a list: wrap each value as an item.
            dashboard_payload[field] = [v for v in val.values() if v is not None]

    # map_data must be dict with expected subkeys
    md = dashboard_payload.get("map_data")
    if not isinstance(md, dict):
        dashboard_payload["map_data"] = {"markers": [], "route_segments": []}
    else:
        if not isinstance(md.get("markers"), list):
            md["markers"] = []
        if not isinstance(md.get("route_segments"), list):
            md["route_segments"] = []


def _normalize_dashboard_aliases(dashboard_payload: dict[str, Any]) -> None:
    if "weather" not in dashboard_payload or dashboard_payload["weather"] is None:
        if "weather_data" in dashboard_payload:
            dashboard_payload["weather"] = dashboard_payload.get("weather_data")

    if not dashboard_payload.get("local_events"):
        if "events" in dashboard_payload:
            dashboard_payload["local_events"] = _as_list(dashboard_payload.get("events"))
        elif "event_api" in dashboard_payload:
            dashboard_payload["local_events"] = _as_list(dashboard_payload.get("event_api"))

    _normalize_dashboard_actions(dashboard_payload)


def _normalize_dashboard_actions(dashboard_payload: dict[str, Any]) -> None:
    """Coerce dashboard_actions list-of-dicts into list-of-strings.

    Fine-tuned model emits action objects like {"type": "show_trip_summary"} or
    {"label": "Book flight", "type": "book_flight", "args": []} instead of plain
    strings. Extract the most specific string key so validation passes.
    """
    actions = dashboard_payload.get("dashboard_actions")
    if not isinstance(actions, list):
        return
    normalized: list[str] = []
    for action in actions:
        if isinstance(action, str) and action.strip():
            normalized.append(action.strip())
        elif isinstance(action, dict):
            key = action.get("type") or action.get("target") or action.get("label")
            if key and isinstance(key, str) and key.strip():
                normalized.append(key.strip())
    dashboard_payload["dashboard_actions"] = normalized


def _normalize_api_grounding(dashboard_payload: dict[str, Any]) -> None:
    api_grounding = dashboard_payload.get("api_grounding")
    if not isinstance(api_grounding, dict):
        api_grounding = {}

    used_api = _canonical_list(api_grounding.get("used_api"), API_ALIASES)
    missing_api = _canonical_list(api_grounding.get("missing_api"), MISSING_API_ALIASES)
    warnings = _as_string_list(api_grounding.get("warnings"))

    api_grounding["used_api"] = [item for item in used_api if item not in missing_api]
    api_grounding["missing_api"] = missing_api
    api_grounding["warnings"] = warnings
    dashboard_payload["api_grounding"] = api_grounding


_ALLOWED_TRIP_SUMMARY_KEYS = frozenset({
    "destination", "origin", "departure_city", "duration_days",
    "travelers", "budget", "currency", "travel_style", "preferences",
})

_NON_FOOD_KEYWORDS = frozenset(
    {
        "transport", "bike", "vélib", "tram", "bus", "metro", "train", "taxi",
        "attraction", "museum", "viewpoint", "monument", "landmark", "tour",
        "area", "sight", "route", "castle", "garden", "park", "palace",
        "square", "bridge", "church", "cathedral", "gallery", "hiking",
        "hotel", "hostel", "stay", "accommodation", "lodging",
        "guesthouse", "apartment", "motel",
        # Additional attraction keywords
        "old town", "town", "philosophers", "way", "path", "street",
        "university", "river", "viewpoint", "tower", "gate", "wall",
        "plaza", "market", "square", "district", "quarter", "neighborhood",
        "beach", "mountain", "hill", "forest", "lake", "waterfall",
        "zoo", "aquarium", "theater", "cinema", "opera", "concert",
        "stadium", "arena", "library", "town hall", "station",
    }
)
_ROOT_API_FIELDS = frozenset({"used_api", "missing_api", "warnings"})

_NO_TRANSPORT_API_NOTE = (
    "Intercity transport cost is not estimated because no live transport API data was provided."
)
_NO_ACCOMMODATION_NOTE = (
    "Accommodation cost is not included because no hotel API data was provided."
)
_STATIC_API_WARNING = (
    "No live transport, hotel, weather, event, or place-rating API data was provided. "
    "Ratings, transport prices, weather, and event details are intentionally not invented."
)

_FAKE_SOURCE_NAMES_SET = frozenset({
    "google reviews", "google maps", "tripadvisor", "wikipedia",
    "booking", "booking.com", "expedia", "skyscanner",
    "local data", "local knowledge", "api", "live data",
    "local source", "open source", "static data",
})

_BUDGET_TRIP_KEYWORDS = frozenset({
    "budget", "cheap", "affordable", "low-cost", "low cost",
    "backpacker", "student", "frugal", "economical",
})

_PAID_ATTRACTION_PATTERNS = [
    re.compile(
        r'\b(?:louvre|musée\s+d.orsay|musee\s+d.orsay|versailles|colosseum|sagrada\s+familia'
        r'|rijksmuseum|prado\s+museum|uffizi|tate\s+modern|british\s+museum)\b',
        re.I,
    ),
    re.compile(r'\b(?:enter|buy|book|purchase)\s+(?:a\s+)?(?:ticket|entry|admission)\b', re.I),
    re.compile(r'\bpaid\s+(?:museum|attraction|entry|admission)\b', re.I),
]

_FAKE_ASSISTANT_PATTERNS = [
    re.compile(r'\bI(?:\'ve)?\s+used\s+(?:a\s+)?local\b', re.I),
    re.compile(
        r'\bbased\s+on\s+(?:live|available|current|local)\s+(?:data|flights?|hotels?|weather|events?)\b',
        re.I,
    ),
    re.compile(r'\bGoogle\s+(?:Reviews|Maps)\b', re.I),
    re.compile(r'\bTripAdvisor\b', re.I),
    re.compile(r'\bWikipedia\b', re.I),
    re.compile(r'\blocal\s+data\s+source\b', re.I),
    re.compile(r'\baccording\s+to\s+(?:google|tripadvisor|local)\b', re.I),
]

_DEFAULT_DASHBOARD_ACTIONS = [
    "show_trip_summary",
    "show_itinerary",
    "show_budget",
    "show_api_warnings",
]

_ALLOWED_PRICE_RANGES = frozenset({"low", "low_to_moderate", "moderate", "high", "unknown"})

_FOOD_ACTIVITY_KEYWORDS = frozenset({
    "food", "cafe", "café", "restaurant", "market", "bakery", "boulangerie",
    "lunch", "dinner", "breakfast", "meal", "snack", "eat",
})

_TRANSPORT_ACTIVITY_KEYWORDS = frozenset({
    "bike rental", "vélib", "velib", "rental", "public transport",
    "metro", "tram", "taxi", "uber",
})


def _deduplicate_itinerary(dashboard_payload: Dict[str, Any]) -> None:
    """Remove exact duplicate (day, time, activity) combos while preserving repeated activities across different days."""
    items = dashboard_payload.get("itinerary")
    if not isinstance(items, list):
        return
    seen: set = set()
    deduped: List[Any] = []
    for item in items:
        if not isinstance(item, dict):
            deduped.append(item)
            continue
        day = str(item.get("day", "")).strip().lower()
        time_slot = re.sub(r"\s+", " ", str(item.get("time", "")).strip().lower())
        activity = re.sub(r"\s+", " ", str(item.get("activity", "")).strip().lower())
        key = (day, time_slot, activity)
        if activity and key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    dashboard_payload["itinerary"] = deduped


def _infer_duration_days_from_message(user_message: str) -> Optional[int]:
    match = re.search(r"\b(\d+)\s*[- ]?\s*day\b", user_message.lower())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _infer_budget_from_message(user_message: str) -> Optional[int]:
    patterns = [
        r"\bunder\s+(\d+)\s*(?:eur|€)?\b",
        r"\b(\d+)\s*(?:eur|€)\s*budget\b",
        r"\bbudget\s*(?:of|is|:)?\s*(\d+)\s*(?:eur|€)?\b",
    ]
    lower = user_message.lower()
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def _infer_destination_from_message(user_message: str) -> Optional[str]:
    patterns = [
        r"\bto\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèêàç\- ]+?)\s+under\b",
        r"\bto\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèêàç\- ]+?)\s+with\b",
        r"\bto\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèêàç\- ]+?)\.?\s*$",
        r"\bin\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüßéèêàç\- ]+?)\s+under\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_message)
        if match:
            destination = re.sub(r"\s+", " ", match.group(1)).strip(" .")
            destination = destination.split(" from ")[0].strip()
            if 2 <= len(destination) <= 40:
                return destination
    return None


_KNOWN_PREFERENCES = [
    "old town walks", "local food", "viewpoints", "parks",
    "free public areas", "cheap public transport", "cafes", "café",
    "street art", "museums from outside", "walking routes", "budget food",
]


def _infer_preferences_from_message(user_message: str) -> List[str]:
    lower = user_message.lower()
    return [pref for pref in _KNOWN_PREFERENCES if pref in lower]


def _sanitize_trip_summary(dashboard_payload: Dict[str, Any], user_message: str = "") -> None:
    """Strip nested/recursive pollution from trip_summary; keep only known flat keys."""
    ts = dashboard_payload.get("trip_summary")
    if not isinstance(ts, dict):
        ts = {}

    cleaned: Dict[str, Any] = {}
    for key in _ALLOWED_TRIP_SUMMARY_KEYS:
        if key not in ts:
            continue
        value = ts[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key] = value
        elif isinstance(value, list):
            # Keep only flat scalar items — drop nested dicts/lists
            cleaned[key] = [
                item for item in value
                if isinstance(item, (str, int, float, bool))
            ]

    # Recover critical fields from user_message when the model output lost them
    if not cleaned.get("duration_days") and user_message:
        duration = _infer_duration_days_from_message(user_message)
        if duration:
            cleaned["duration_days"] = duration

    if not cleaned.get("budget") and user_message:
        budget = _infer_budget_from_message(user_message)
        if budget:
            cleaned["budget"] = budget

    if not cleaned.get("destination") and user_message:
        destination = _infer_destination_from_message(user_message)
        if destination:
            cleaned["destination"] = destination

    if not cleaned.get("preferences") and user_message:
        prefs = _infer_preferences_from_message(user_message)
        if prefs:
            cleaned["preferences"] = prefs

    cleaned.setdefault("currency", "EUR")
    dashboard_payload["trip_summary"] = cleaned


def _hotel_names_from_context(api_context: Optional[Dict[str, Any]]) -> set:
    """Return normalized hotel names from api_context so they can be excluded from food/itinerary."""
    ctx = api_context or {}
    names: set = set()
    for h in _as_list(ctx.get("hotels")):
        if isinstance(h, dict):
            name = _normalized_text(h.get("name"))
            if name:
                names.add(name)
    return names


def _filter_non_food_recommendations(
    dashboard_payload: Dict[str, Any],
    api_context: Optional[Dict[str, Any]],
) -> None:
    """Remove food_recommendations entries that are clearly non-food (transport, attractions, hotels, etc.)."""
    items = dashboard_payload.get("food_recommendations")
    if not isinstance(items, list):
        return
    hotel_names = _hotel_names_from_context(api_context)
    filtered: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        combined = " ".join(
            str(item.get(k, "")).lower()
            for k in ("name", "type", "category", "description")
        )
        if any(kw in combined for kw in _NON_FOOD_KEYWORDS):
            continue
        if hotel_names and _normalized_text(item.get("name")) in hotel_names:
            continue
        filtered.append(item)
    
    # Limit to max 5 recommendations, prioritize first unique ones
    seen_names = set()
    limited_filtered = []
    for item in filtered:
        if isinstance(item, dict) and "name" in item:
            name = _normalized_text(item["name"])
            if name not in seen_names:
                seen_names.add(name)
                limited_filtered.append(item)
                if len(limited_filtered) >= 5:
                    break
        else:
            limited_filtered.append(item)
            if len(limited_filtered) >= 5:
                break
    
    dashboard_payload["food_recommendations"] = limited_filtered


_REAL_APIS = frozenset({"flights", "hotels", "weather", "events", "local_events"})


def _normalize_live_api_section(value: Any, default_data: Any) -> Dict[str, Any]:
    """Normalize live API section to canonical structure with data/source/status."""
    if isinstance(value, dict) and "data" in value:
        data = value.get("data")
        if data is None:
            data = default_data
        status = value.get("status")
        return {
            "data": data,
            "source": "live_api",
            "status": status or ("available" if _has_live_data(data) else "unavailable"),
        }

    data = value if value is not None else default_data
    return {
        "data": data,
        "source": "live_api",
        "status": "available" if _has_live_data(data) else "unavailable",
    }


def _remove_legacy_duplicate_fields(dashboard_payload: Dict[str, Any]) -> None:
    """Remove legacy duplicate fields from final response."""
    # Remove legacy fields that should not appear in final response
    legacy_fields = [
        "flight",
        "flight_options", 
        "stay_recommendations",
        "hotel_options",
        "weather_data",
        "events",
    ]
    for field in legacy_fields:
        dashboard_payload.pop(field, None)


def _strip_root_api_fields(dashboard_payload: Dict[str, Any]) -> None:
    """Remove root-level used_api/missing_api/warnings that the model sometimes leaks.

    The model occasionally outputs these fields at the dashboard_payload root instead of
    (or in addition to) the correct api_grounding sub-object. Before final validation,
    merge any real API names into api_grounding and then delete the root copies.
    'local knowledge' and other non-API strings are discarded.
    """
    ag = dashboard_payload.setdefault("api_grounding", {"used_api": [], "missing_api": [], "warnings": []})

    # Merge root used_api — only keep canonical API names, discard "local knowledge" etc.
    root_used = dashboard_payload.get("used_api")
    if isinstance(root_used, list):
        for item in root_used:
            canonical = str(item).strip().lower()
            if canonical in _REAL_APIS and canonical not in ag.get("used_api", []):
                ag.setdefault("used_api", []).append(canonical)

    # Merge root missing_api similarly
    root_missing = dashboard_payload.get("missing_api")
    if isinstance(root_missing, list):
        for item in root_missing:
            canonical = str(item).strip().lower()
            if canonical in _REAL_APIS and canonical not in ag.get("missing_api", []):
                ag.setdefault("missing_api", []).append(canonical)

    # Remove root-level fields — they must not appear at dashboard_payload root
    for field in _ROOT_API_FIELDS:
        dashboard_payload.pop(field, None)


# ── Detection helpers ─────────────────────────────────────────────────────────

def _has_api_data(api_context: Optional[Dict[str, Any]], key: str) -> bool:
    if not isinstance(api_context, dict):
        return False
    val = api_context.get(key)
    if val is None:
        return False
    if isinstance(val, (list, dict)):
        return bool(val)
    return True


def _has_places_api(api_context: Optional[Dict[str, Any]]) -> bool:
    return any(
        _has_api_data(api_context, k)
        for k in ("places", "restaurants", "reviews", "google_maps", "ratings")
    )


def _is_budget_trip(user_message: str) -> bool:
    lower = user_message.lower()
    return any(kw in lower for kw in _BUDGET_TRIP_KEYWORDS)


def _is_budget_from_summary(dashboard_payload: Dict[str, Any]) -> bool:
    ts = dashboard_payload.get("trip_summary", {})
    if not isinstance(ts, dict):
        return False
    return any(kw in str(ts.get("travel_style", "")).lower() for kw in _BUDGET_TRIP_KEYWORDS)


def _parse_eur_amount(value: Any) -> Optional[float]:
    """Extract a numeric amount from values like '40 EUR', '€40', 40, '40'."""
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if isinstance(value, str):
        cleaned = re.sub(r"[€£$EUReur\s,]", "", value.strip())
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _parse_price_number(price_str: str, default: int = 0) -> int:
    """Extract the first number from a price string like '$250-350' -> 250."""
    if not price_str:
        return default
    
    match = re.search(r'\d+', str(price_str))
    if match:
        return int(match.group())
    return default


def _get_cheapest_flight_price_from_payload(payload: dict) -> int:
    flights_section = payload.get("flights") or {}
    flights_data = flights_section.get("data") or []

    prices = []

    for flight in flights_data:
        price_value = None

        if isinstance(flight, dict):
            price_value = flight.get("price")
        else:
            price_value = getattr(flight, "price", None)

        price_number = _parse_price_number(price_value, 0)

        if price_number > 0:
            prices.append(price_number)

    return min(prices) if prices else 0


# ── Sanitization functions ────────────────────────────────────────────────────

def _normalize_price_range(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    _LOW = {"budget", "budget_friendly", "cheap", "low_cost", "low", "affordable", "very_low", "free"}
    _LOW_MID = {"low_to_moderate", "moderate_low", "mid_range", "midrange", "inexpensive"}
    _MID = {"moderate", "medium", "average", "mid"}
    _HIGH = {"high", "expensive", "luxury", "premium", "upscale", "fine_dining"}
    if raw in _LOW:
        return "low"
    if raw in _LOW_MID:
        return "low_to_moderate"
    if raw in _MID:
        return "moderate"
    if raw in _HIGH:
        return "high"
    if raw in _ALLOWED_PRICE_RANGES:
        return raw

    numeric_prices = [int(number) for number in re.findall(r"\d+", str(value or ""))]
    if numeric_prices:
        first_price = numeric_prices[0]
        if first_price <= 10:
            return "low"
        if first_price <= 20:
            return "low_to_moderate"
        if first_price <= 40:
            return "moderate"
        return "high"

    return "unknown"


def _infer_food_type(item: Dict[str, Any]) -> str:
    name = str(item.get("name", "")).strip().lower()
    combined = " ".join(
        str(item.get(k, "")).lower()
        for k in ("name", "type", "category", "description", "area")
    )
    if "market" in combined:
        return "market"
    if "cafe" in combined or "café" in combined or "coffee" in combined:
        return "cafe"
    if "bakery" in combined or "boulangerie" in combined:
        return "bakery"
    if (
        "restaurant" in combined
        or "bistro" in combined
        or "brasserie" in combined
        or name.startswith(("le ", "la ", "les ", "au ", "chez "))
    ):
        return "restaurant"
    if "snack" in combined or "street food" in combined:
        return "snack"
    return "food"


def _has_transport_api(api_context: Optional[Dict[str, Any]]) -> bool:
    ctx = api_context or {}
    return bool(ctx.get("flights") or ctx.get("trains") or ctx.get("buses") or ctx.get("transport"))


def _sanitize_food_recommendations(
    dashboard_payload: Dict[str, Any],
    api_context: Optional[Dict[str, Any]],
    user_message: str,
) -> None:
    """Rules 1, 2, 4: null ratings, replace fake sources, normalize type/price, remove expensive for budget."""
    items = dashboard_payload.get("food_recommendations")
    if not isinstance(items, list):
        return

    has_places = _has_places_api(api_context)
    is_budget = _is_budget_trip(user_message) or _is_budget_from_summary(dashboard_payload)

    filtered: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        item = dict(item)

        # Infer type if missing or empty
        item["type"] = str(item.get("type") or _infer_food_type(item)).strip().lower()

        # Normalize price_range to allowed vocabulary
        item["price_range"] = _normalize_price_range(item.get("price_range"))

        # Rule 4: skip expensive items on budget trips
        if is_budget and item["price_range"] == "high":
            continue

        # Rule 1: null out invented ratings and remove invented price field
        if not has_places:
            item["rating"] = None
            item.pop("price", None)

        # Rule 2: replace fake source names
        source = str(item.get("source", "")).strip().lower()
        if not source or source in _FAKE_SOURCE_NAMES_SET or not has_places:
            item["source"] = "static_model_suggestion"

        filtered.append(item)

    dashboard_payload["food_recommendations"] = filtered


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


_FOOD_ITEM_TYPES = frozenset({
    "food", "restaurant", "cafe", "bakery", "market", "snack", "street_food", "boulangerie",
})


def _food_recommendation_lookup(dashboard_payload: Dict[str, Any]) -> set:
    """Return normalized names of food_recommendations whose type is a food category."""
    items = dashboard_payload.get("food_recommendations")
    if not isinstance(items, list):
        return set()
    result: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _normalized_text(item.get("name"))
        food_type = _normalized_text(item.get("type"))
        if name and food_type in _FOOD_ITEM_TYPES:
            result.add(name)
    return result


def _sanitize_itinerary_costs(
    dashboard_payload: Dict[str, Any],
    api_context: Optional[Dict[str, Any]],
) -> None:
    """Rules 5, 6: Null budget_eur for paid attractions, food stops, and transport with no API."""
    items = dashboard_payload.get("itinerary")
    if not isinstance(items, list):
        return

    ctx = api_context or {}
    has_price_api = bool(ctx.get("attraction_prices") or ctx.get("prices"))
    has_transport = _has_transport_api(api_context)
    food_names = _food_recommendation_lookup(dashboard_payload)
    hotel_names = _hotel_names_from_context(api_context)

    for item in items:
        if not isinstance(item, dict):
            continue

        activity_raw = str(item.get("activity", ""))
        activity = _normalized_text(activity_raw)

        # Strict food price fix: null budget_eur for food activities regardless of current
        # value — catches model-invented prices (e.g. budget_eur: 10) not just 0.
        if not has_price_api and item.get("budget_eur") is not None:
            matched_food = any(name and name in activity for name in food_names)
            if matched_food or any(kw in activity for kw in _FOOD_ACTIVITY_KEYWORDS):
                item["budget_eur"] = None
                continue

        if item.get("budget_eur") != 0:
            continue

        # Fix 5: rewrite hotel check-in activities to a neutral city-walk suggestion
        if hotel_names and any(name and name in activity for name in hotel_names):
            item["budget_eur"] = None
            item["activity"] = "Check in or leave bags at accommodation, then start a budget city walk."
            continue

        # Rule 5: paid attractions → null
        if not has_price_api:
            for pattern in _PAID_ATTRACTION_PATTERNS:
                if pattern.search(activity):
                    item["budget_eur"] = None
                    break

        # Food stops → null: match by food_recommendation name OR keyword
        if item.get("budget_eur") == 0:
            matched_food = any(name and name in activity for name in food_names)
            if matched_food or any(kw in activity for kw in _FOOD_ACTIVITY_KEYWORDS):
                item["budget_eur"] = None

        # Transport/rental without transport API → null + note
        if item.get("budget_eur") == 0 and not has_transport:
            if any(kw in activity for kw in _TRANSPORT_ACTIVITY_KEYWORDS):
                item["budget_eur"] = None
                if "check" not in activity and "price" not in activity:
                    item["activity"] = f"{activity_raw} — check current price separately."


def _ensure_minimum_itinerary(dashboard_payload: Dict[str, Any]) -> None:
    """If itinerary is empty after sanitization, generate a safe per-day fallback."""
    itinerary = dashboard_payload.get("itinerary")
    if isinstance(itinerary, list) and itinerary:
        return

    ts = dashboard_payload.get("trip_summary", {})
    destination = "the city"
    duration = 1
    if isinstance(ts, dict):
        destination = str(ts.get("destination") or destination)
        try:
            duration = int(ts.get("duration_days") or 1)
        except (TypeError, ValueError):
            duration = 1

    duration = max(1, min(duration, 7))
    fallback: List[Dict[str, Any]] = []
    for day in range(1, duration + 1):
        fallback.extend([
            {
                "day": day,
                "time": "Morning",
                "activity": f"Start with a budget walking route in {destination}.",
                "budget_eur": 0,
            },
            {
                "day": day,
                "time": "Afternoon",
                "activity": "Visit free or low-cost public areas and local neighborhoods.",
                "budget_eur": 0,
            },
            {
                "day": day,
                "time": "Evening",
                "activity": "Choose a simple local food stop and check prices before ordering.",
                "budget_eur": None,
            },
        ])
    dashboard_payload["itinerary"] = fallback


def _sanitize_budget_breakdown(
    dashboard_payload: Dict[str, Any],
    api_context: Optional[Dict[str, Any]],
    user_message: str,
) -> None:
    """Rules 6, 7: Null transport/accommodation costs when APIs are missing; compute total_known_cost."""
    bb = dashboard_payload.get("budget_breakdown")
    if not isinstance(bb, dict):
        return

    ctx = api_context or {}
    has_transport = _has_transport_api(api_context)
    transport_verified = ctx.get("flight_source") == "live_api"
    has_hotels = bool(ctx.get("hotels"))

    ts = dashboard_payload.get("trip_summary", {})
    duration = 1
    currency = "EUR"
    if isinstance(ts, dict):
        try:
            duration = int(ts.get("duration_days", 1) or 1)
        except (TypeError, ValueError):
            duration = 1
        if ts.get("currency"):
            currency = str(ts["currency"]).upper()

    # Always stamp currency on budget_breakdown
    bb["currency"] = bb.get("currency") or currency

    ag = dashboard_payload.setdefault("api_grounding", {"used_api": [], "missing_api": [], "warnings": []})
    new_warnings: List[str] = []

    # Rule 6: Extract transport cost from flights data when available
    if has_transport:
        transport_cost = _get_cheapest_flight_price_from_payload(
            {"flights": {"data": _as_list(ctx.get("flights"))}}
        )
        if transport_cost > 0:
            bb["transport"] = transport_cost
            bb["intercity_transport"] = transport_cost
        else:
            bb["transport"] = None
            bb["intercity_transport"] = None
            if not any(_NO_TRANSPORT_API_NOTE in w for w in ag.get("warnings", [])):
                new_warnings.append(_NO_TRANSPORT_API_NOTE)
    else:
        bb["transport"] = None
        bb["intercity_transport"] = None
        if not any(_NO_TRANSPORT_API_NOTE in w for w in ag.get("warnings", [])):
            new_warnings.append(_NO_TRANSPORT_API_NOTE)

    # Use the cheapest live stay total; never sum alternative hotel options.
    accommodation_known = False
    if has_hotels:
        hotel_totals: list[float] = []
        for hotel in _as_list(ctx.get("hotels")):
            if not isinstance(hotel, dict):
                continue
            total = _parse_eur_amount(hotel.get("price_total"))
            if total is None:
                nightly = _parse_eur_amount(hotel.get("price_per_night"))
                total = nightly * max(duration - 1, 1) if nightly is not None else None
            if total is not None and total > 0:
                hotel_totals.append(total)
        if hotel_totals:
            bb["accommodation"] = round(min(hotel_totals))
            accommodation_known = True

    # Rule 7: multi-day and no hotels → null accommodation
    if not has_hotels and duration > 1:
        bb["accommodation"] = None
        if not any(_NO_ACCOMMODATION_NOTE in w for w in ag.get("warnings", [])):
            new_warnings.append(_NO_ACCOMMODATION_NOTE)

    for w in new_warnings:
        ag.setdefault("warnings", []).append(w)

    # Model costs are estimates; only API-backed transport/accommodation are known.
    _KNOWN_COST_KEYS = (
        "food", "activities", "accommodation", "local_transport",
        "entertainment", "cafe", "museums", "sightseeing",
    )
    estimated_total = 0.0
    for k in _KNOWN_COST_KEYS:
        # Only count accommodation when it comes from real hotel data
        if k == "accommodation" and not accommodation_known:
            continue
        amt = _parse_eur_amount(bb.get(k))
        if amt is not None and amt >= 0:
            estimated_total += amt

    # Add transport cost if available
    transport_cost = bb.get("transport")
    if transport_cost is not None and isinstance(transport_cost, (int, float)) and transport_cost > 0:
        estimated_total += float(transport_cost)

    verified_total = 0.0
    if accommodation_known:
        verified_total += _parse_eur_amount(bb.get("accommodation")) or 0
    if (
        transport_verified
        and transport_cost is not None
        and isinstance(transport_cost, (int, float))
        and transport_cost > 0
    ):
        verified_total += float(transport_cost)

    null_keys = {k for k in ("accommodation",) if bb.get(k) is None}
    if null_keys:
        bb.pop("total", None)
        bb["total_known_cost"] = round(verified_total)
        bb["total_estimated_cost"] = round(estimated_total)
    else:
        bb["total"] = round(estimated_total)
        bb["total_known_cost"] = round(verified_total)
        bb["total_estimated_cost"] = round(estimated_total)

        budget_raw = ts.get("budget") if isinstance(ts, dict) else None
        budget = _parse_eur_amount(budget_raw)
        if budget is not None and budget > 0:
            # Calculate remaining budget
            bb["remaining_budget"] = round(max(0.0, budget - estimated_total))
            bb["within_budget"] = budget >= estimated_total
            
            # Legacy keys for compatibility
            if not accommodation_known:
                bb["remaining_budget_before_transport_and_accommodation"] = bb["remaining_budget"]
            else:
                bb["remaining_budget_before_transport"] = bb["remaining_budget"]

        missing_parts: List[str] = []
        if not has_transport:
            missing_parts.append("intercity transport")
        if not has_hotels and duration > 1:
            missing_parts.append("accommodation")
        if missing_parts:
            joined = " and ".join(missing_parts)
            bb["note"] = f"{joined.capitalize()} cost is not estimated because no live API data was provided."


def _strip_invalid_exchange_rate(
    dashboard_payload: Dict[str, Any],
    api_context: Optional[Dict[str, Any]],
) -> None:
    """Rule 8: Remove currency_exchange_rate: 0 — it is not meaningful without an FX API."""
    for key in ("currency_exchange_rate", "exchange_rate"):
        val = dashboard_payload.get(key)
        if val == 0 or val is None:
            dashboard_payload.pop(key, None)
    for sub in ("trip_summary", "budget_breakdown"):
        d = dashboard_payload.get(sub)
        if isinstance(d, dict):
            for key in ("currency_exchange_rate", "exchange_rate"):
                if d.get(key) == 0 or (key in d and d[key] is None):
                    d.pop(key, None)


def _ensure_dashboard_actions(dashboard_payload: dict[str, Any]) -> None:
    """Rule 11: Merge model actions with defaults, preserving all unique entries in order."""
    actions = dashboard_payload.get("dashboard_actions")
    if not isinstance(actions, list):
        actions = []
    merged = list(actions)
    for default_action in _DEFAULT_DASHBOARD_ACTIONS:
        if default_action not in merged:
            merged.append(default_action)
    dashboard_payload["dashboard_actions"] = merged


_WARNING_NORMALIZATIONS: dict[str, str] = {
    "compact retry used.": "Compact retry was used — first response was incomplete JSON.",
    "compact retry was used": "Compact retry was used — first response was incomplete JSON.",
}


def _dedupe_warnings(dashboard_payload: dict[str, Any]) -> None:
    """Deduplicate api_grounding.warnings and normalize known variant phrasings."""
    ag = dashboard_payload.get("api_grounding")
    if not isinstance(ag, dict):
        return
    warnings = ag.get("warnings")
    if not isinstance(warnings, list):
        return

    seen: set[str] = set()
    deduped: list[str] = []
    for w in warnings:
        text = str(w).strip()
        key = text.lower()
        for pattern, replacement in _WARNING_NORMALIZATIONS.items():
            if pattern in key:
                text = replacement
                key = replacement.lower()
                break
        if key not in seen:
            seen.add(key)
            deduped.append(text)

    ag["warnings"] = deduped


def _add_static_grounding_warning(dashboard_payload: dict[str, Any]) -> None:
    """Add professor-safe static API warnings; ensure transport and places are in missing_api."""
    ag = dashboard_payload.get("api_grounding", {})
    missing_api = ag.setdefault("missing_api", [])
    warnings = ag.setdefault("warnings", [])

    if "transport" not in missing_api:
        missing_api.insert(0, "transport")

    if "places" not in missing_api:
        missing_api.append("places")

    missing = set(missing_api)
    all_missing = (
        {"flights", "hotels", "weather", "events"}.issubset(missing)
        or {"transport", "hotels", "weather", "events"}.issubset(missing)
    )
    if all_missing and not any(_STATIC_API_WARNING in w for w in warnings):
        warnings.insert(0, _STATIC_API_WARNING)


def _sanitize_assistant_message(normalized: Dict[str, Any], api_context: Optional[Dict[str, Any]]) -> None:
    """Rule 9: Replace fake data claims; produce context-aware message for partial API contexts."""
    msg = normalized.get("assistant_message", "")
    if not isinstance(msg, str):
        return

    ctx = api_context or {}
    has_flights = bool(ctx.get("flights"))
    has_hotels = bool(ctx.get("hotels"))
    has_weather = ctx.get("weather") is not None
    has_events = bool(ctx.get("local_events"))
    all_missing = not (has_flights or has_hotels or has_weather or has_events)

    has_fake_claim = any(p.search(msg) for p in _FAKE_ASSISTANT_PATTERNS)

    if not has_fake_claim and not all_missing:
        return

    dp = normalized.get("dashboard_payload", {})
    ts = dp.get("trip_summary", {}) if isinstance(dp, dict) else {}
    dest = ts.get("destination", "") if isinstance(ts, dict) else ""
    duration = ts.get("duration_days", "") if isinstance(ts, dict) else ""

    trip_part = "your trip"
    if duration and dest:
        trip_part = f"your {duration}-day trip to {dest}"
    elif dest:
        trip_part = f"your trip to {dest}"

    if all_missing:
        disclaimer = (
            "No live API data was provided, so live prices, weather, events, transport, "
            "and hotel availability should be checked separately."
        )
        normalized["assistant_message"] = (
            f"Here is a static planning suggestion for {trip_part}. {disclaimer}"
        )
    else:
        used_parts = []
        if has_weather:
            used_parts.append("weather")
        if has_flights:
            used_parts.append("flight")
        if has_hotels:
            used_parts.append("hotel")
        if has_events:
            used_parts.append("event")

        missing_parts = []
        if not has_flights:
            missing_parts.append("flight")
        if not has_hotels:
            missing_parts.append("hotel")
        if not has_weather:
            missing_parts.append("weather")
        if not has_events:
            missing_parts.append("event")

        used_str = " and ".join(used_parts)
        missing_str = ", ".join(missing_parts)
        normalized["assistant_message"] = (
            f"Here is a planning suggestion for {trip_part} using provided {used_str} data. "
            f"Live {missing_str} data was not available — those fields are intentionally empty."
        )


def _canonical_list(value: Any, aliases: Dict[str, str]) -> List[str]:
    result: list[str] = []
    for item in _as_string_list(value):
        normalized = aliases.get(item.strip().lower(), item.strip().lower())
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _as_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
