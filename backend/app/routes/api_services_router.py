from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, List

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from backend.app.core.api_errors import ApiContractException
from backend.app.core.observability import log_operation_failure
from backend.app.core.request_controls import generation_request_guard
from backend.app.core.security import require_api_key
from backend.app.models.chat_models import (
    ApiErrorResponse,
    ChatMessage,
    ChatResponse as OldChatResponse,
    WeatherInfo,
    FlightInfo,
    HotelInfo,
    EventInfo,
    TravelItinerary,
)
from backend.app.services.weather_service import WeatherService
from backend.app.services.flight_service import FlightService
from backend.app.services.hotel_service import HotelService
from backend.app.services.events_service import EventsService
from backend.app.services.itinerary_service import ItineraryService

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["api-services"])

# Initialize services
weather_service = WeatherService()
flight_service = FlightService()
hotel_service = HotelService()
events_service = EventsService()
itinerary_service = ItineraryService()
SERVICE_ERROR_RESPONSES = {
    401: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
    500: {"model": ApiErrorResponse},
    503: {"model": ApiErrorResponse},
}


class ChatRequest(BaseModel):
    """Chat request model for old API compatibility"""
    message: str
    session_id: str
    destination: str = None
    days: int = None
    budget: str = None


@router.get(
    "/weather/{city}",
    dependencies=[Depends(require_api_key)],
    responses=SERVICE_ERROR_RESPONSES,
)
async def get_weather(
    city: Annotated[str, Path(min_length=1, max_length=120)]
):
    """Get current weather for a city"""
    try:
        weather = await weather_service.get_current_weather(city)
        if not weather:
            return {"message": f"Weather information not available for {city}", "data": None}
        return {"message": "Weather data retrieved successfully", "data": weather}
    except Exception as exc:
        log_operation_failure(logger, operation="weather_route", error=exc)
        raise HTTPException(status_code=500, detail="Error fetching weather information")


@router.post(
    "/flights/search",
    dependencies=[Depends(require_api_key)],
    responses=SERVICE_ERROR_RESPONSES,
)
async def search_flights(
    origin: Annotated[str, Query(min_length=1, max_length=120)],
    destination: Annotated[str, Query(min_length=1, max_length=120)],
    departure_date: date,
    return_date: date | None = None,
):
    """Search for flights between two cities"""
    try:
        if return_date is not None and return_date < departure_date:
            raise ApiContractException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INVALID_DATE_RANGE",
                message="Return date must not be before the departure date.",
                retryable=False,
            )
        flights = await flight_service.search_flights(
            origin,
            destination,
            departure_date.isoformat(),
            return_date.isoformat() if return_date else None,
        )
        if not flights:
            return {"message": "No flights found for the specified criteria", "data": []}
        return {"message": "Flights found successfully", "data": flights}
    except ApiContractException:
        raise
    except Exception as exc:
        log_operation_failure(logger, operation="flight_search_route", error=exc)
        raise HTTPException(status_code=500, detail="Error searching flights")


@router.post(
    "/hotels/search",
    dependencies=[Depends(require_api_key)],
    responses=SERVICE_ERROR_RESPONSES,
)
async def search_hotels(
    destination: Annotated[str, Query(min_length=1, max_length=120)],
    check_in: date,
    check_out: date,
    guests: Annotated[int, Query(ge=1, le=10)] = 1,
):
    """Search for hotels in a destination"""
    try:
        if check_out <= check_in:
            raise ApiContractException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INVALID_DATE_RANGE",
                message="Check-out date must be after the check-in date.",
                retryable=False,
            )
        hotels = await hotel_service.search_hotels(
            destination,
            check_in.isoformat(),
            check_out.isoformat(),
            guests,
        )
        if not hotels:
            return {"message": "No hotels found for the specified criteria", "data": []}
        return {"message": "Hotels found successfully", "data": hotels}
    except ApiContractException:
        raise
    except Exception as exc:
        log_operation_failure(logger, operation="hotel_search_route", error=exc)
        raise HTTPException(status_code=500, detail="Error searching hotels")


@router.get(
    "/events/{city}",
    dependencies=[Depends(require_api_key)],
    responses=SERVICE_ERROR_RESPONSES,
)
async def get_events(
    city: Annotated[str, Path(min_length=1, max_length=120)],
    start_date: date | None = None,
    end_date: date | None = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
):
    """Get local events in a city"""
    try:
        if start_date and end_date and end_date < start_date:
            raise ApiContractException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="INVALID_DATE_RANGE",
                message="End date must not be before the start date.",
                retryable=False,
            )
        start_date_value = start_date.isoformat() if start_date else None
        end_date_value = end_date.isoformat() if end_date else None
        events = await events_service.search_events(
            city, start_date_value, end_date_value, category
        )
        if not events:
            return {
                "message": f"No events found in {city} for the specified criteria",
                "data": [],
                "city": city,
                "start_date": start_date_value,
                "end_date": end_date_value,
                "category": category
            }

        # Format events for better response
        formatted_events = []
        for event in events:
            formatted_events.append({
                "name": event.name,
                "date": event.date,
                "time": event.date.split(" at ")[1] if " at " in event.date else "Time not specified",  
                "venue": event.location.split(", ")[0] if ", " in event.location else event.location,   
                "city": city.title(),
                "country": event.location.split(", ") [-1] if ", " in event.location else "Unknown",    
                "category": event.category,
                "price": event.price,
                "url": event.url
            })

        return {
            "message": f"Found {len(formatted_events)} events in {city}",
            "data": formatted_events,
            "total": len(formatted_events)
        }
    except ApiContractException:
        raise
    except Exception as exc:
        log_operation_failure(logger, operation="events_route", error=exc)
        raise HTTPException(status_code=500, detail="Error fetching events")


@router.post(
    "/itinerary/generate",
    dependencies=[
        Depends(require_api_key),
        Depends(generation_request_guard),
    ],
    responses={
        **SERVICE_ERROR_RESPONSES,
        429: {"model": ApiErrorResponse},
    },
)
async def generate_itinerary(
    destination: Annotated[str, Query(min_length=1, max_length=120)],
    duration_days: Annotated[int, Query(ge=1, le=7)],
    preferences: Annotated[
        List[Annotated[str, Field(min_length=1, max_length=80)]] | None,
        Body(max_length=10),
    ] = None,
):
    """Generate a travel itinerary"""
    try:
        itinerary = await itinerary_service.generate_itinerary(
            destination, duration_days, preferences or []
        )
        return {"message": "Itinerary generated successfully", "data": itinerary}
    except Exception as exc:
        log_operation_failure(logger, operation="itinerary_route", error=exc)
        raise HTTPException(status_code=500, detail="Error generating itinerary")


@router.get(
    "/chat/history/{session_id}",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    response_model=ApiErrorResponse,
    deprecated=True,
    dependencies=[Depends(require_api_key)],
    responses={501: {"model": ApiErrorResponse}},
)
async def get_chat_history(
    session_id: Annotated[
        str,
        Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"),
    ],
) -> ApiErrorResponse:
    """Disabled until authenticated ownership and persistence are available."""
    raise ApiContractException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="HISTORY_DISABLED",
        message="Chat history is disabled until secure persistence is available.",
        retryable=False,
    )


@router.delete(
    "/chat/history/{session_id}",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    response_model=ApiErrorResponse,
    deprecated=True,
    dependencies=[Depends(require_api_key)],
    responses={501: {"model": ApiErrorResponse}},
)
async def clear_chat_history(
    session_id: Annotated[
        str,
        Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"),
    ],
) -> ApiErrorResponse:
    """Disabled until authenticated ownership and persistence are available."""
    raise ApiContractException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        code="HISTORY_DISABLED",
        message="Chat history is disabled until secure persistence is available.",
        retryable=False,
    )


@router.get("/health", deprecated=True)
async def health_check():
    """Legacy compatibility alias; use the root /health endpoint."""
    return {"status": "healthy", "service": "api services router"}
