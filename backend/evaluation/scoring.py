from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.models.chat_models import ChatResponse
from backend.evaluation.contracts import (
    EvaluationCase,
    EvaluationLane,
    EvaluationTurn,
    HardFailureCode,
    ModelVariant,
    ProviderFixture,
    ProviderName,
    ScoreMetrics,
)


class _StrictItineraryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=10)
    time: str
    activity: str
    budget_eur: int | float


class _StrictFoodItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    price_range: str
    type: str


class _StrictItineraryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    itinerary: list[_StrictItineraryItem]
    food_recommendations: list[_StrictFoodItem] = Field(max_length=3)


def parse_strict_json_object(raw_output: str) -> dict[str, Any]:
    parsed = json.loads(raw_output)
    if not isinstance(parsed, dict):
        raise ValueError("model_output_root_must_be_object")
    return parsed


def _extract_json_object_for_evidence(raw_output: str) -> dict[str, Any] | None:
    """Best-effort claim extraction that never changes strict validity."""
    candidate = raw_output.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        candidate,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced is not None:
        candidate = fenced.group(1)
    else:
        object_start = candidate.find("{")
        if object_start < 0:
            return None
        candidate = candidate[object_start:]
    try:
        parsed, _ = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _iter_key_values(value: Any, keys: set[str]) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in keys:
                yield key, nested
            yield from _iter_key_values(nested, keys)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_key_values(item, keys)


def _normalized_values(value: Any, keys: set[str]) -> set[str]:
    return {
        json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()
        for _, item in _iter_key_values(value, keys)
        if item is not None and item != ""
    }


def _fixture_allowed_values(fixture: ProviderFixture, keys: set[str]) -> set[str]:
    return _normalized_values(fixture.results, keys)


def _extract_grounding_ids(value: Any) -> set[str]:
    result: set[str] = set()
    for key, item in _iter_key_values(value, {"id", "grounding_id", "provider_id", "poi_id"}):
        if isinstance(item, str) and item:
            result.add(item)
    return result


def _response_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    fragments: list[str] = []
    for key, item in _iter_key_values(
        value,
        {"assistant_message", "activity", "name", "description"},
    ):
        if isinstance(item, str):
            fragments.append(item)
    return " ".join(fragments)


def _has_unknown_values(
    actual: Any,
    fixture: ProviderFixture,
    keys: set[str],
) -> bool:
    actual_values = _normalized_values(actual, keys)
    if not actual_values:
        return False
    allowed_values = _fixture_allowed_values(fixture, keys)
    return bool(actual_values - allowed_values)


def _dashboard_section(value: Any, section: str) -> Any:
    if not isinstance(value, Mapping):
        return {}
    dashboard = value.get("dashboard_payload", value)
    if not isinstance(dashboard, Mapping):
        return {}
    selected = dashboard.get(section, {})
    if isinstance(selected, Mapping) and "data" in selected:
        return selected.get("data")
    return selected


def _route_claim_without_fixture(actual: Any, fixture: ProviderFixture) -> bool:
    route_keys = {"distance", "duration", "duration_minutes", "travel_time"}
    if _has_unknown_values(actual, fixture, route_keys):
        return True
    text = _response_text(actual)
    route_pattern = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:km|kilomet(?:er|re)s?|miles?|minutes?|mins?|hours?|hrs?)\b",
        re.IGNORECASE,
    )
    allowed_text = _response_text(fixture.results)
    return bool(route_pattern.search(text)) and not bool(route_pattern.search(allowed_text))


def _expected_structure_valid(intent: str, parsed: dict[str, Any] | None) -> bool:
    if parsed is None:
        return False
    if intent == "itinerary_generation":
        try:
            _StrictItineraryOutput.model_validate(parsed)
        except ValidationError:
            return False
        return True
    return "assistant_message" in parsed and "dashboard_payload" in parsed


def score_turn(
    *,
    case: EvaluationCase,
    turn: EvaluationTurn,
    lane: EvaluationLane,
    requested_model: ModelVariant,
    raw_outputs: list[str],
    public_response: dict[str, Any] | None,
    fixture: ProviderFixture,
    provider_call_counts: dict[ProviderName, int],
    loaded_variants: list[ModelVariant],
) -> tuple[ScoreMetrics, list[HardFailureCode]]:
    parsed: dict[str, Any] | None = None
    strict_json_valid = False
    if raw_outputs:
        try:
            parsed = parse_strict_json_object(raw_outputs[-1])
            strict_json_valid = True
        except (json.JSONDecodeError, ValueError):
            pass

    evidence_value: Any
    if public_response is not None:
        evidence_value = public_response
    elif parsed is not None:
        evidence_value = parsed
    elif raw_outputs:
        evidence_value = _extract_json_object_for_evidence(raw_outputs[-1]) or raw_outputs[-1]
    else:
        evidence_value = {}
    public_schema_valid: bool | None = None
    if lane == EvaluationLane.APPLICATION:
        if public_response is not None:
            try:
                ChatResponse.model_validate(public_response)
                public_schema_valid = True
            except ValidationError:
                public_schema_valid = False

    text = _response_text(evidence_value).casefold()
    expectation = turn.expectation
    required_terms_present = all(term.casefold() in text for term in expectation.must_include_terms)
    forbidden_terms_absent = all(term.casefold() not in text for term in expectation.must_not_include_terms)
    language_match = None
    if expectation.language_markers:
        language_match = any(marker.casefold() in text for marker in expectation.language_markers)

    actual_ids = _extract_grounding_ids(evidence_value)
    allowed_ids = set(fixture.authoritative_ids) | set(expectation.allowed_grounding_ids)
    grounding_ids_valid = actual_ids <= allowed_ids
    selected_ids_match = None
    if expectation.expected_selected_ids:
        selected_ids_match = set(expectation.expected_selected_ids) <= actual_ids

    provider_calls_match = None
    if lane == EvaluationLane.APPLICATION:
        called = {name for name, count in provider_call_counts.items() if count}
        provider_calls_match = called == set(expectation.expected_provider_calls)

    selected_model = public_response.get("selected_model") if public_response else None
    intent = None
    if public_response:
        dashboard = public_response.get("dashboard_payload")
        if isinstance(dashboard, dict):
            intent = dashboard.get("intent")
    elif parsed:
        dashboard = parsed.get("dashboard_payload", parsed)
        if isinstance(dashboard, dict):
            intent = dashboard.get("intent") or (
                "itinerary_generation" if "itinerary" in dashboard else None
            )

    failures: list[HardFailureCode] = []
    flight_keys = {"airline", "flight_number"}
    hotel_keys = {"name"}
    price_keys = {"price", "price_per_night", "price_total", "total_price"}
    coordinate_keys = {"lat", "lng", "latitude", "longitude"}
    if _has_unknown_values(evidence_value, fixture, flight_keys):
        failures.append(HardFailureCode.FABRICATED_FLIGHT)
    if _has_unknown_values(
        _dashboard_section(evidence_value, "hotels"),
        fixture,
        hotel_keys,
    ):
        failures.append(HardFailureCode.FABRICATED_HOTEL)
    if _has_unknown_values(evidence_value, fixture, price_keys):
        failures.append(HardFailureCode.FABRICATED_PRICE)
    if _route_claim_without_fixture(evidence_value, fixture):
        failures.append(HardFailureCode.FABRICATED_ROUTE)
    if _has_unknown_values(evidence_value, fixture, coordinate_keys):
        failures.append(HardFailureCode.FABRICATED_COORDINATES)
    if not grounding_ids_valid:
        failures.append(HardFailureCode.INVALID_GROUNDING_ID)
    if (
        lane == EvaluationLane.APPLICATION
        and public_response is not None
        and selected_model != requested_model.value
    ):
        failures.append(HardFailureCode.WRONG_SELECTED_MODEL)
    if loaded_variants and any(variant != requested_model for variant in loaded_variants):
        failures.append(HardFailureCode.SILENT_MODEL_FALLBACK)
    if public_schema_valid is False:
        failures.append(HardFailureCode.PUBLIC_SCHEMA_FAILURE)

    scores = ScoreMetrics(
        strict_json_valid=strict_json_valid,
        expected_structure_valid=_expected_structure_valid(expectation.intent, parsed),
        public_schema_valid=public_schema_valid,
        intent_match=intent == expectation.intent,
        required_terms_present=required_terms_present,
        forbidden_terms_absent=forbidden_terms_absent,
        grounding_ids_valid=grounding_ids_valid,
        selected_ids_match=selected_ids_match,
        language_heuristic_match=language_match,
        linguistic_review_required=case.linguistic_review_required,
        provider_calls_match=provider_calls_match,
    )
    return scores, list(dict.fromkeys(failures))
