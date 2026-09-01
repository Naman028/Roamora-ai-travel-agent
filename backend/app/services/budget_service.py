"""Source-aware travel budget calculations."""

from __future__ import annotations

import re
from statistics import mean
from typing import Any


DEFAULT_DAILY_FOOD_EUR = 25.0
PRICE_RANGE_DAILY_ESTIMATES = {
    "low": 15.0,
    "low_to_moderate": 25.0,
    "moderate": 40.0,
    "high": 70.0,
}
VERIFIED_SOURCE = "live_api"


def parse_amount(value: Any) -> float | None:
    """Return the first non-negative monetary amount in a value."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None

    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group().replace(",", "."))


def infer_currency(value: Any, fallback: str = "EUR") -> str:
    """Infer an ISO currency from a monetary value without converting it."""
    text = str(value or "").upper()
    for currency in ("EUR", "USD", "GBP"):
        if currency in text:
            return currency
    if "€" in text:
        return "EUR"
    if "$" in text:
        return "USD"
    if "£" in text:
        return "GBP"
    return fallback.upper()


def _rounded(value: float) -> int | float:
    rounded = float(round(float(value), 2))
    return int(rounded) if rounded.is_integer() else rounded


def _section(payload: dict[str, Any], name: str) -> tuple[list[Any], str]:
    section = payload.get(name)
    if isinstance(section, dict):
        data = section.get("data")
        source = str(section.get("source") or "unknown")
    else:
        data = section
        source = "unknown"
    if not isinstance(data, list):
        data = [data] if data else []
    return data, source


def _cost_detail(
    amount: float,
    currency: str,
    pricing_basis: str,
    source: str,
) -> dict[str, Any]:
    return {
        "amount": _rounded(amount),
        "currency": currency,
        "pricing_basis": pricing_basis,
        "source": source,
        "verified": source == VERIFIED_SOURCE,
    }


def _cheapest_flight(
    payload: dict[str, Any], target_currency: str, warnings: list[str]
) -> dict[str, Any] | None:
    flights, section_source = _section(payload, "flights")
    candidates: list[dict[str, Any]] = []
    for flight in flights:
        if not isinstance(flight, dict):
            continue
        raw_price = flight.get("price_total", flight.get("price"))
        amount = parse_amount(raw_price)
        if not amount:
            continue
        currency = str(flight.get("currency") or infer_currency(raw_price, target_currency)).upper()
        if currency != target_currency:
            warnings.append(
                f"Flight price in {currency} was excluded because no {currency}-to-{target_currency} conversion is configured."
            )
            continue
        source = str(flight.get("source") or section_source)
        candidates.append(
            _cost_detail(amount, currency, "quoted_lower_bound", source)
        )
    return min(candidates, key=lambda item: item["amount"], default=None)


def _cheapest_hotel(
    payload: dict[str, Any],
    target_currency: str,
    nights: int,
    warnings: list[str],
) -> dict[str, Any] | None:
    hotels, section_source = _section(payload, "hotels")
    candidates: list[dict[str, Any]] = []
    for hotel in hotels:
        if not isinstance(hotel, dict):
            continue

        total_value = hotel.get("price_total", hotel.get("total_cost"))
        nightly_value = hotel.get("price_per_night")
        pricing_basis = str(hotel.get("pricing_basis") or "").lower()
        if total_value is not None or pricing_basis == "total_stay":
            raw_price = total_value if total_value is not None else hotel.get("price")
            amount = parse_amount(raw_price)
            basis = "total_stay"
        else:
            raw_price = nightly_value if nightly_value is not None else hotel.get("price")
            nightly_amount = parse_amount(raw_price)
            amount = nightly_amount * nights if nightly_amount is not None else None
            basis = "per_night"

        if not amount:
            continue
        currency = str(hotel.get("currency") or infer_currency(raw_price, target_currency)).upper()
        if currency != target_currency:
            warnings.append(
                f"Hotel price in {currency} was excluded because no {currency}-to-{target_currency} conversion is configured."
            )
            continue
        source = str(hotel.get("source") or section_source)
        candidates.append(_cost_detail(amount, currency, basis, source))
    return min(candidates, key=lambda item: item["amount"], default=None)


def _food_estimate(payload: dict[str, Any], duration_days: int, currency: str) -> dict[str, Any]:
    existing_budget = payload.get("budget_breakdown")
    if isinstance(existing_budget, dict):
        model_food = parse_amount(existing_budget.get("food"))
        if model_food:
            return _cost_detail(model_food, currency, "trip_estimate", "model_estimate")

    daily_estimates: list[float] = []
    for item in payload.get("food_recommendations") or []:
        if not isinstance(item, dict):
            continue
        price_range = item.get("price_range")
        numeric = parse_amount(price_range)
        if numeric:
            daily_estimates.append(numeric)
            continue
        normalized = str(price_range or "").lower().replace("-", "_").replace(" ", "_")
        if normalized in PRICE_RANGE_DAILY_ESTIMATES:
            daily_estimates.append(PRICE_RANGE_DAILY_ESTIMATES[normalized])

    daily = mean(daily_estimates) if daily_estimates else DEFAULT_DAILY_FOOD_EUR
    return _cost_detail(daily * duration_days, currency, "trip_estimate", "model_estimate")


def _selected_event_cost(
    payload: dict[str, Any], currency: str, warnings: list[str]
) -> tuple[dict[str, Any], set[str]]:
    events, section_source = _section(payload, "local_events")
    activities = [
        str(item.get("activity") or "").lower()
        for item in payload.get("itinerary") or []
        if isinstance(item, dict)
    ]
    matched_names: set[str] = set()
    total = 0.0
    source = section_source
    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("name") or "").strip().lower()
        if not name or name in matched_names or not any(name in activity for activity in activities):
            continue
        raw_price = event.get("price")
        amount = parse_amount(raw_price)
        if not amount:
            continue
        event_currency = str(event.get("currency") or infer_currency(raw_price, currency)).upper()
        if event_currency != currency:
            warnings.append(
                f"Event price in {event_currency} was excluded because no {event_currency}-to-{currency} conversion is configured."
            )
            continue
        matched_names.add(name)
        total += amount
        source = str(event.get("source") or section_source)
    if not matched_names:
        return _cost_detail(0, currency, "not_selected", "not_applicable"), matched_names
    return _cost_detail(total, currency, "selected_events", source), matched_names


def _activity_estimate(
    payload: dict[str, Any], currency: str, matched_event_names: set[str]
) -> dict[str, Any]:
    amount = sum(
        parse_amount(item.get("budget_eur")) or 0
        for item in payload.get("itinerary") or []
        if isinstance(item, dict)
        and not any(
            name in str(item.get("activity") or "").lower()
            for name in matched_event_names
        )
    )
    return _cost_detail(amount, currency, "trip_estimate", "model_estimate")


def calculate_budget_breakdown(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate compatible totals while separating verified and estimated costs."""
    intent = str(payload.get("intent") or "itinerary_generation")
    trip_summary = payload.get("trip_summary") or {}
    try:
        duration_days = max(int(trip_summary.get("duration_days") or 1), 1)
    except (TypeError, ValueError):
        duration_days = 1
    nights = max(duration_days - 1, 1)
    currency = str(trip_summary.get("currency") or "EUR").upper()
    total_budget = parse_amount(trip_summary.get("budget")) or 500.0
    warnings: list[str] = []

    flight = _cheapest_flight(payload, currency, warnings)
    hotel = _cheapest_hotel(payload, currency, nights, warnings)
    zero = _cost_detail(0, currency, "not_applicable", "not_applicable")

    if intent == "weather_query":
        flight = hotel = None
        food = activities = events = zero
    elif intent == "flight_search":
        hotel = None
        food = activities = events = zero
    elif intent == "hotel_search":
        flight = None
        food = activities = events = zero
    else:
        food = _food_estimate(payload, duration_days, currency)
        events, matched_event_names = _selected_event_cost(payload, currency, warnings)
        activities = _activity_estimate(payload, currency, matched_event_names)

    details = {
        "transport": flight or zero,
        "accommodation": hotel or zero,
        "food": food,
        "activities": activities,
        "events": events,
    }
    total_estimated = sum(float(item["amount"]) for item in details.values())
    total_known = sum(
        float(item["amount"]) for item in details.values() if item["verified"]
    )
    model_estimated = total_estimated - total_known
    remaining = total_budget - total_estimated

    note = (
        "Verified live prices and unverified model or fallback estimates are shown separately. "
        "No currency conversion is performed."
    )
    return {
        "currency": currency,
        "transport": details["transport"]["amount"],
        "food": details["food"]["amount"],
        "activities": details["activities"]["amount"],
        "accommodation": details["accommodation"]["amount"],
        "intercity_transport": details["transport"]["amount"],
        "total_known_cost": _rounded(total_known),
        "total_estimated_cost": _rounded(total_estimated),
        "model_estimated_total": _rounded(model_estimated),
        "total": _rounded(total_estimated),
        "remaining_budget": _rounded(remaining),
        "remaining_budget_before_transport_and_accommodation": _rounded(
            total_budget - float(details["food"]["amount"]) - float(details["activities"]["amount"])
        ),
        "within_budget": remaining >= 0,
        "cost_details": details,
        "calculation_warnings": list(dict.fromkeys(warnings)),
        "note": note,
        "source": "backend_budget_calculation",
    }
