from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ModelVariant = Literal["base", "fine_tuned"]
MoneyValue = float | int | str | None


class PublicResponseModel(BaseModel):
    """Public response base that filters fields not declared in the contract."""

    model_config = ConfigDict(extra="ignore")


class WeatherInfo(BaseModel):
    """Weather information used by the direct service endpoint."""

    location: str
    temperature: float
    description: str
    humidity: int
    wind_speed: float


class FlightInfo(BaseModel):
    """Flight information used by the direct service endpoint."""

    origin: str
    destination: str
    departure_date: str
    return_date: Optional[str] = None
    price: Optional[str] = None
    airline: Optional[str] = None
    flight_number: Optional[str] = None


class HotelInfo(BaseModel):
    """Hotel information used by the direct service endpoint."""

    name: str
    location: str
    price_per_night: Optional[float | str] = None
    price_total: Optional[float] = None
    currency: Optional[str] = None
    pricing_basis: Optional[Literal["per_night", "total_stay"]] = None
    source: str = "live_api"
    rating: Optional[float] = None
    amenities: List[str] = Field(default_factory=list)


class EventInfo(BaseModel):
    """Local event information used by the direct service endpoint."""

    name: str
    date: str
    location: str
    category: str
    price: Optional[str] = None
    url: Optional[str] = None


class ChatMessage(BaseModel):
    """A temporary, non-persistent chat message."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)
    timestamp: Optional[datetime] = None


class TravelItinerary(BaseModel):
    """Basic itinerary returned by the direct itinerary service."""

    destination: str
    duration_days: int = Field(ge=1, le=7)
    activities: List[str]
    accommodation: Optional[str] = None
    transportation: Optional[str] = None
    estimated_budget: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        default="demo-user-1",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    message: str = Field(min_length=1, max_length=4000)
    model_variant: ModelVariant = "fine_tuned"
    max_new_tokens: Optional[int] = Field(default=None, ge=384, le=1800)
    include_raw_model_output: Literal[False] = Field(
        default=False,
        description="Raw model output is disabled on the public API.",
    )
    api_context: Dict[str, Any] = Field(
        default_factory=lambda: {
            "flights": [],
            "hotels": [],
            "weather": None,
            "local_events": [],
        }
    )


class TripSummary(PublicResponseModel):
    destination: str | None = None
    duration_days: int | None = None
    travelers: int | str | None = None
    budget: MoneyValue = None
    origin: str | None = None
    currency: str | None = None
    source: str | None = None


class WeatherResult(PublicResponseModel):
    location: str | None = None
    city: str | None = None
    temperature: float | None = None
    temperature_c: float | None = None
    description: str | None = None
    condition: str | None = None
    humidity: int | None = None
    wind_speed: float | None = None


class FlightResult(PublicResponseModel):
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    return_date: str | None = None
    price: MoneyValue = None
    currency: str | None = None
    airline: str | None = None
    flight_number: str | None = None
    source: str | None = None


class HotelResult(PublicResponseModel):
    name: str | None = None
    location: str | None = None
    price_per_night: MoneyValue = None
    price_total: MoneyValue = None
    price_range: str | None = None
    currency: str | None = None
    pricing_basis: Literal["per_night", "total_stay"] | None = None
    source: str | None = None
    rating: float | None = None
    amenities: list[str] = Field(default_factory=list)


class EventResult(PublicResponseModel):
    name: str | None = None
    date: str | None = None
    time: str | None = None
    location: str | None = None
    venue: str | None = None
    city: str | None = None
    country: str | None = None
    category: str | None = None
    price: MoneyValue = None
    url: str | None = None


class FoodRecommendation(PublicResponseModel):
    name: str
    type: str | None = None
    price_range: str | None = None
    source: str | None = None
    rating: float | None = None


class ItineraryItem(PublicResponseModel):
    day: int
    time: str | None = None
    activity: str
    location: str | None = None
    budget_eur: float | int | None = None
    source: str | None = None


class MapMarker(PublicResponseModel):
    label: str
    type: str = "place"
    lat: float | None = None
    lng: float | None = None


class RouteSegment(PublicResponseModel):
    origin: str | None = None
    destination: str | None = None
    transport: str | None = None
    distance: str | None = None


class MapData(PublicResponseModel):
    markers: list[MapMarker] = Field(default_factory=list)
    route_segments: list[RouteSegment | str] = Field(default_factory=list)


class CostDetail(PublicResponseModel):
    amount: float = 0
    currency: str = "EUR"
    source: str = "not_applicable"
    confidence: str = "not_applicable"
    verified: bool = False
    pricing_basis: str | None = None


class CostDetails(PublicResponseModel):
    transport: CostDetail = Field(default_factory=CostDetail)
    accommodation: CostDetail = Field(default_factory=CostDetail)
    food: CostDetail = Field(default_factory=CostDetail)
    activities: CostDetail = Field(default_factory=CostDetail)
    events: CostDetail = Field(default_factory=CostDetail)


class BudgetBreakdown(PublicResponseModel):
    currency: str | None = None
    transport: MoneyValue = None
    food: MoneyValue = None
    activities: MoneyValue = None
    accommodation: MoneyValue = None
    intercity_transport: MoneyValue = None
    total_known_cost: MoneyValue = None
    total_estimated_cost: MoneyValue = None
    model_estimated_total: MoneyValue = None
    total: MoneyValue = None
    remaining_budget: MoneyValue = None
    remaining_budget_before_transport_and_accommodation: MoneyValue = None
    within_budget: bool | None = None
    cost_details: CostDetails | None = None
    calculation_warnings: list[str] = Field(default_factory=list)
    note: str | None = None
    source: str | None = None


class ApiGrounding(PublicResponseModel):
    used_api: list[str] = Field(default_factory=list)
    missing_api: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WeatherSection(PublicResponseModel):
    data: WeatherResult | None = None
    source: str = "unavailable"
    status: str = "unavailable"


class FlightSection(PublicResponseModel):
    data: list[FlightResult] = Field(default_factory=list)
    source: str = "unavailable"
    status: str = "unavailable"


class HotelSection(PublicResponseModel):
    data: list[HotelResult] = Field(default_factory=list)
    source: str = "unavailable"
    status: str = "unavailable"


class EventSection(PublicResponseModel):
    data: list[EventResult] = Field(default_factory=list)
    source: str = "unavailable"
    status: str = "unavailable"


class DashboardPayload(PublicResponseModel):
    schema_version: Literal["travel_dashboard_v1"] = "travel_dashboard_v1"
    intent: str
    trip_summary: TripSummary = Field(default_factory=TripSummary)
    weather: WeatherSection | None = None
    flights: FlightSection = Field(default_factory=FlightSection)
    hotels: HotelSection = Field(default_factory=HotelSection)
    local_events: EventSection = Field(default_factory=EventSection)
    food_recommendations: list[FoodRecommendation] = Field(default_factory=list)
    itinerary: list[ItineraryItem] = Field(default_factory=list)
    map_data: MapData = Field(default_factory=MapData)
    budget_breakdown: BudgetBreakdown = Field(default_factory=BudgetBreakdown)
    dashboard_actions: list[str] = Field(default_factory=list)
    api_grounding: ApiGrounding = Field(default_factory=ApiGrounding)
    itinerary_source: str | None = None
    assistant_message_source: str | None = None


class ChatResponse(PublicResponseModel):
    session_id: str
    selected_model: ModelVariant
    adapter_loaded: bool
    parse_success: bool
    fallback_used: bool
    retry_used: bool
    assistant_message: str
    dashboard_payload: DashboardPayload


class FieldError(PublicResponseModel):
    field: str
    message: str


class ApiError(PublicResponseModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    field_errors: list[FieldError] = Field(default_factory=list)


class ApiErrorResponse(PublicResponseModel):
    error: ApiError
