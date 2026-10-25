from __future__ import annotations

import copy
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from backend.evaluation.contracts import (
    EvaluationTurn,
    ProviderFixture,
    ProviderName,
    ProviderState,
)


@dataclass
class ProviderCallRecorder:
    counts: dict[ProviderName, int] = field(
        default_factory=lambda: {name: 0 for name in ProviderName}
    )

    def record(self, provider: ProviderName) -> None:
        self.counts[provider] += 1


class EvaluationNetworkAccessError(RuntimeError):
    """Raised when an evaluation attempts an external network connection."""


@contextmanager
def blocked_external_network() -> Iterator[None]:
    """Fail closed if model or application code attempts a socket connection."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def blocked(*_args, **_kwargs):
        raise EvaluationNetworkAccessError("evaluation_external_network_blocked")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.create_connection = original_create_connection


def _provider_result(fixture: ProviderFixture, provider: ProviderName):
    state = fixture.states[provider]
    if state == ProviderState.TIMEOUT:
        raise TimeoutError(f"fixture_{provider.value}_timeout")
    if state == ProviderState.UNAVAILABLE:
        return None
    if state == ProviderState.EMPTY:
        return None if provider == ProviderName.WEATHER else []
    return copy.deepcopy(fixture.results[provider])


class _WeatherDouble:
    def __init__(self, fixture: ProviderFixture, recorder: ProviderCallRecorder):
        self.fixture = fixture
        self.recorder = recorder

    async def get_current_weather(self, *_args, **_kwargs):
        self.recorder.record(ProviderName.WEATHER)
        return _provider_result(self.fixture, ProviderName.WEATHER)


class _FlightDouble:
    def __init__(self, fixture: ProviderFixture, recorder: ProviderCallRecorder):
        self.fixture = fixture
        self.recorder = recorder

    async def search_flights(self, *_args, **_kwargs):
        self.recorder.record(ProviderName.FLIGHTS)
        return _provider_result(self.fixture, ProviderName.FLIGHTS)


class _HotelDouble:
    def __init__(self, fixture: ProviderFixture, recorder: ProviderCallRecorder):
        self.fixture = fixture
        self.recorder = recorder

    async def search_hotels(self, *_args, **_kwargs):
        self.recorder.record(ProviderName.HOTELS)
        return _provider_result(self.fixture, ProviderName.HOTELS)


class _EventsDouble:
    def __init__(self, fixture: ProviderFixture, recorder: ProviderCallRecorder):
        self.fixture = fixture
        self.recorder = recorder

    async def search_events(self, *_args, **_kwargs):
        self.recorder.record(ProviderName.EVENTS)
        return _provider_result(self.fixture, ProviderName.EVENTS)


@contextmanager
def controlled_provider_services(
    fixture: ProviderFixture,
) -> Iterator[ProviderCallRecorder]:
    """Replace provider clients only; application orchestration remains unchanged."""
    from backend.app.services import api_context_service as context_module
    from backend.app.services import ai_model_service as model_module
    from backend.app.services import events_service as events_module
    from backend.app.services import flight_service as flight_module
    from backend.app.services import hotel_service as hotel_module
    from backend.app.services import weather_service as weather_module

    service = context_module.api_context_service
    recorder = ProviderCallRecorder()
    weather_double = _WeatherDouble(fixture, recorder)
    flight_double = _FlightDouble(fixture, recorder)
    hotel_double = _HotelDouble(fixture, recorder)
    events_double = _EventsDouble(fixture, recorder)
    original_services = (
        service._weather_service,
        service._flight_service,
        service._hotel_service,
        service._events_service,
    )
    original_factories = (
        weather_module.WeatherService,
        flight_module.FlightService,
        hotel_module.HotelService,
        events_module.EventsService,
        model_module.hotel_service,
    )
    service._weather_service = weather_double
    service._flight_service = flight_double
    service._hotel_service = hotel_double
    service._events_service = events_double
    weather_module.WeatherService = lambda: weather_double
    flight_module.FlightService = lambda: flight_double
    hotel_module.HotelService = lambda: hotel_double
    events_module.EventsService = lambda: events_double
    model_module.hotel_service = hotel_double
    try:
        yield recorder
    finally:
        (
            service._weather_service,
            service._flight_service,
            service._hotel_service,
            service._events_service,
        ) = original_services
        (
            weather_module.WeatherService,
            flight_module.FlightService,
            hotel_module.HotelService,
            events_module.EventsService,
            model_module.hotel_service,
        ) = original_factories


def build_model_lane_context(
    turn: EvaluationTurn,
    fixture: ProviderFixture,
) -> dict[str, object]:
    """Build deterministic grounding without invoking application providers."""
    context: dict[str, object] = {
        "weather": None,
        "flights": [],
        "hotels": [],
        "local_events": [],
        "used_apis": [],
        "missing_apis": [],
        "warnings": [],
        "travel_info": turn.travel_info.model_dump(mode="json"),
    }
    used_apis: list[str] = []
    missing_apis: list[str] = []
    for provider in ProviderName:
        state = fixture.states[provider]
        context_key = "local_events" if provider == ProviderName.EVENTS else provider.value
        if state == ProviderState.SUCCESS:
            context[context_key] = copy.deepcopy(fixture.results[provider])
            source_key = "event_source" if provider == ProviderName.EVENTS else f"{provider.value.rstrip('s')}_source"
            context[source_key] = "live_api"
            used_apis.append(context_key)
        else:
            missing_apis.append(context_key)
            context["warnings"].append(
                f"Evaluation fixture: {provider.value} state is {state.value}."
            )
    context["used_apis"] = used_apis
    context["missing_apis"] = missing_apis
    return context


def fixed_timestamp() -> datetime:
    return datetime.fromisoformat("2030-01-01T00:00:00+00:00")


@contextmanager
def controlled_application_clock(fixed_utc: datetime) -> Iterator[None]:
    """Freeze only the application module's clock for reproducible defaults."""
    from backend.app.services import api_context_service as context_module

    original_datetime = context_module.datetime

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_utc.replace(tzinfo=None)
            return fixed_utc.astimezone(tz)

    context_module.datetime = _FixedDateTime
    try:
        yield
    finally:
        context_module.datetime = original_datetime
