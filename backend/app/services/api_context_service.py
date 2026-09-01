from __future__ import annotations

import asyncio
import re
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.app.models.chat_models import (
    WeatherInfo,
    FlightInfo,
    HotelInfo,
    EventInfo,
)
from backend.app.services.weather_service import WeatherService
from backend.app.services.flight_service import FlightService
from backend.app.services.hotel_service import HotelService
from backend.app.services.events_service import EventsService

logger = logging.getLogger(__name__)


class ApiContextService:
    """Service to build and enrich API context from user messages"""

    def __init__(self):
        self._weather_service = None
        self._flight_service = None
        self._hotel_service = None
        self._events_service = None
        
        # City alias map for typo correction
        self.city_aliases = {
            "sttugart": "Stuttgart",
            "stuttgart": "Stuttgart",
            "berlin": "Berlin",
            "munich": "Munich",
            "münchen": "Munich",
            "heidelberg": "Heidelberg"
        }

    def normalize_city(self, city: str) -> str:
        """Normalize city name to proper format with typo correction"""
        if not city:
            return city
        
        city = city.strip().lower()
        
        # Use city alias map for typo correction
        return self.city_aliases.get(city, city.title())

    @property
    def weather_service(self):
        if self._weather_service is None:
            self._weather_service = WeatherService()
        return self._weather_service

    @property
    def flight_service(self):
        if self._flight_service is None:
            self._flight_service = FlightService()
        return self._flight_service

    @property
    def hotel_service(self):
        if self._hotel_service is None:
            self._hotel_service = HotelService()
        return self._hotel_service

    @property
    def events_service(self):
        if self._events_service is None:
            self._events_service = EventsService()
        return self._events_service

    def extract_travel_info(self, message: str) -> Dict[str, Any]:
        """Extract travel information from user message"""
        message_lower = message.lower()
        
        # Extended city list including German cities
        cities = [
            "stuttgart", "berlin", "munich", "hamburg", "frankfurt", "cologne", "düsseldorf",
            "heidelberg", "bonn", "leipzig", "dresden", "nuremberg", "bremen", "hannover",
            "paris", "london", "rome", "madrid", "amsterdam", "vienna", "brussels",
            "prague", "budapest", "warsaw", "zurich", "barcelona", "milan", "lisbon",
            "auckland", "beijing", "chicago", "copenhagen", "krakow", "los angeles",
            "marrakech", "marseille", "miami", "porto", "santiago", "seoul",
            "singapore", "tokyo"
        ]
        
        # Find all cities mentioned in order of appearance
        found_cities = []
        for city in cities:
            if city in message_lower:
                # Find position to maintain order
                start_pos = message_lower.find(city)
                normalized_city = self.normalize_city(city)
                found_cities.append((start_pos, normalized_city))
        
        # Sort by position in message
        found_cities.sort(key=lambda x: x[0])
        cities_mentioned = [city for _, city in found_cities]
        
        # Extract duration with more patterns
        duration_patterns = [
            r'(\d+)\s*[- ]?\s*day',
            r'(\d+)\s*[- ]?\s*days?',
            r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\s*[- ]?\s*days?\b',
            r'weekend',  # Special case for weekend trips
            r'(\d+)\s*[- ]?\s*night'
        ]
        
        duration_days = 2  # default
        for pattern in duration_patterns:
            match = re.search(pattern, message_lower)
            if match:
                if pattern == r'weekend':
                    duration_days = 2
                elif match.group(1).isdigit():
                    duration_days = int(match.group(1))
                else:
                    duration_days = {
                        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                    }[match.group(1)]
                break
        
        # Extract budget style
        budget_keywords = {
            "budget": ["budget", "cheap", "affordable", "low cost", "economy"],
            "mid": ["moderate", "mid-range", "reasonable", "standard"],
            "luxury": ["luxury", "premium", "high-end", "expensive", "deluxe"]
        }
        
        detected_budget = "budget"  # default
        for budget_type, keywords in budget_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                detected_budget = budget_type
                break

        budget_match = (
            re.search(r'(?:budget|under|below|max(?:imum)?)\s*(?:of\s*)?(?:eur|â‚¬)?\s*(\d+(?:[.,]\d+)?)', message_lower)
            or re.search(r'(?:eur|â‚¬)\s*(\d+(?:[.,]\d+)?)', message_lower)
            or re.search(r'(\d+(?:[.,]\d+)?)\s*(?:eur|â‚¬)', message_lower)
        )
        requested_budget: float | str = detected_budget
        if budget_match:
            requested_budget = float(budget_match.group(1).replace(",", "."))
            if requested_budget.is_integer():
                requested_budget = int(requested_budget)
        
        def city_from_match(match: Optional[re.Match]) -> Optional[str]:
            if not match:
                return None
            return self.normalize_city(match.group(1))

        def mentioned_city_for(value: Optional[str]) -> Optional[str]:
            if not value:
                return None
            normalized_value = value.strip().lower()
            for city in cities_mentioned:
                if city.lower() == normalized_value:
                    return city
            return None

        # Determine origin and destination based on context
        origin = None
        destination = None
        
        if len(cities_mentioned) == 1:
            # Single city - assume it's the destination
            destination = cities_mentioned[0]
            origin = "Berlin"  # Default origin for German context
        elif len(cities_mentioned) >= 2:
            from_city = city_from_match(
                re.search(r'\bfrom\s+([^\s,.?!]+)', message_lower)
                or re.search(r'\bcoming\s+from\s+([^\s,.?!]+)', message_lower)
                or re.search(r'\bdeparting\s+from\s+([^\s,.?!]+)', message_lower)
            )
            to_city = city_from_match(
                re.search(r'\bto\s+([^\s,.?!]+)', message_lower)
                or re.search(r'\bin\s+([^\s,.?!]+)', message_lower)
            )

            if from_city:
                origin = mentioned_city_for(from_city)
            if to_city and to_city != origin:
                destination = mentioned_city_for(to_city)

            if origin and not destination:
                destination = next((city for city in cities_mentioned if city != origin), cities_mentioned[-1])
            elif destination and not origin:
                origin = next((city for city in cities_mentioned if city != destination), cities_mentioned[0])
            elif origin and destination:
                pass
            else:
                # Default: first city is origin, last is destination
                origin = cities_mentioned[0]
                destination = cities_mentioned[-1]
        
        # Fallbacks if still not found
        if not destination:
            destination = "Unknown"
        if not origin:
            origin = "Berlin"
        
        # Extract dates (simplified - look for YYYY-MM-DD or common patterns)
        today = datetime.now()
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', message)
        if date_match:
            departure_date = date_match.group(1)
        else:
            # Default to tomorrow
            departure_date = (today + timedelta(days=1)).strftime('%Y-%m-%d')
        
        return_date = None
        if duration_days > 1:
            return_date = (today + timedelta(days=duration_days)).strftime('%Y-%m-%d')
        
        return {
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "duration_days": duration_days,
            "check_in": departure_date,
            "check_out": return_date or (today + timedelta(days=duration_days + 1)).strftime('%Y-%m-%d'),
            "guests": 2,  # Default assumption
            "budget": requested_budget
        }

    def detect_user_intent(self, message: str) -> Dict[str, bool]:
        """
        Detect user intent to only call relevant services
        """
        message_lower = message.lower()
        
        intent = {
            "flights": False,
            "hotels": False, 
            "weather": False,
            "events": False
        }
        
        # Itinerary keywords (highest priority)
        itinerary_keywords = [
            "plan", "itinerary", "trip", "day", "days", "budget trip", "travel plan", 
            "schedule", "route", "vacation", "weekend trip"
        ]
        
        # Flight keywords (lower priority - explicit flight words only)
        flight_keywords = [
            "flight", "fly", "flying", "airline", "airport", "depart", "arrival", 
            "ticket", "booking", "airfare", "plane"
        ]
        
        # Hotel keywords  
        hotel_keywords = [
            "hotel", "stay", "accommodation", "room", "booking", "check in", "check out",
            "resort", "lodging", "sleep", "night"
        ]
        
        # Weather keywords
        weather_keywords = [
            "weather", "temperature", "rain", "sunny", "cloudy", "forecast",
            "climate", "conditions", "hot", "cold"
        ]
        
        # Events keywords
        events_keywords = [
            "event", "activity", "things to do", "attraction", "museum", "concert",
            "festival", "show", "entertainment", "tour", "sightseeing"
        ]
        
        # Detect intents with priority order (itinerary first!)
        itinerary_detected = any(keyword in message_lower for keyword in itinerary_keywords)
        weather_detected = any(keyword in message_lower for keyword in weather_keywords)
        flight_detected = any(keyword in message_lower for keyword in flight_keywords)
        hotel_detected = any(keyword in message_lower for keyword in hotel_keywords)
        events_detected = any(keyword in message_lower for keyword in events_keywords)
        
        # Priority: itinerary_generation > weather_query > flight_search > hotel_search > events_search
        if itinerary_detected:
            intent["itinerary_generation"] = True
        elif weather_detected:
            intent["weather_query"] = True
        elif flight_detected:
            intent["flight_search"] = True
        elif hotel_detected:
            intent["hotel_search"] = True
        elif events_detected:
            intent["events_search"] = True
        else:
            # Default fallback
            intent["itinerary_generation"] = True
            
        return intent

    async def build_api_context_from_message(
        self, 
        message: str, 
        existing_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build API context from message and existing context
        
        If existing_context has data, use it. Otherwise, enrich with API calls.
        """
        # Check if context already has meaningful data
        has_existing_data = (
            existing_context.get("flights") or 
            existing_context.get("hotels") or 
            existing_context.get("weather") or 
            existing_context.get("local_events")
        )
        
        if has_existing_data:
            # Client-provided context may be useful for planning, but it is not
            # proof that a live provider returned the data.
            provided_context = dict(existing_context)
            if provided_context.get("weather"):
                provided_context["weather_source"] = "provided_context"
            if provided_context.get("flights"):
                provided_context["flight_source"] = "provided_context"
            if provided_context.get("hotels"):
                provided_context["hotel_source"] = "provided_context"
            if provided_context.get("local_events"):
                provided_context["event_source"] = "provided_context"
            warnings = list(provided_context.get("warnings") or [])
            warnings.append("Client-provided travel data is unverified and is not counted as live API pricing.")
            provided_context["warnings"] = list(dict.fromkeys(warnings))
            provided_context.setdefault("travel_info", self.extract_travel_info(message))
            provided_context.setdefault("detected_intent", self.detect_user_intent(message))
            return provided_context
        
        # Extract travel info from message
        travel_info = self.extract_travel_info(message)
        
        # Detect user intent to only call relevant services
        intent = self.detect_user_intent(message)
        
        # Build enriched context
        enriched_context = {
            "flights": [],
            "hotels": [],
            "weather": None,
            "local_events": [],
            "warnings": [],
            "used_apis": [],
            "missing_apis": [],
            "travel_info": travel_info,  # Include extracted travel information
            "detected_intent": intent  # Include detected intent for debugging
        }
        
        # For itinerary requests, automatically fetch weather, hotels, and events
        # For explicit requests, only fetch the requested service
        is_itinerary = intent.get("itinerary_generation", False)
        destination = travel_info.get("destination")
        origin = travel_info.get("origin")
        departure_date = travel_info.get("departure_date", "14/10/2026")
        return_date = travel_info.get("return_date", "17/10/2026")
        check_in = travel_info.get("check_in", "2026-05-11")
        check_out = travel_info.get("check_out", "2026-05-13")
        guests = travel_info.get("guests", 2)

        weather_task = (
            asyncio.create_task(self.weather_service.get_current_weather(destination))
            if destination and (is_itinerary or intent.get("weather", False))
            else None
        )
        flight_task = (
            asyncio.create_task(
                self.flight_service.search_flights(
                    origin=origin,
                    destination=destination,
                    departure_date=departure_date,
                    return_date=return_date,
                )
            )
            if origin and destination and (is_itinerary or intent.get("flights", False))
            else None
        )
        hotel_task = (
            asyncio.create_task(
                self.hotel_service.search_hotels(destination, check_in, check_out, guests)
            )
            if destination and (is_itinerary or intent.get("hotels", False))
            else None
        )
        events_task = (
            asyncio.create_task(
                self.events_service.search_events(destination, departure_date, return_date)
            )
            if destination and (is_itinerary or intent.get("events", False))
            else None
        )
        
        # Weather: fetch for itinerary or explicit weather request
        if is_itinerary or intent.get("weather", False):
            try:
                weather = await weather_task if weather_task else None
                if weather:
                    enriched_context["weather"] = weather.dict() if hasattr(weather, 'dict') else weather
                    enriched_context["weather_source"] = "live_api"
                    enriched_context["used_apis"].append("weather")
                else:
                    enriched_context["warnings"].append("Weather service unavailable - API key missing")
                    enriched_context["missing_apis"].append("weather")
            except Exception as exc:
                logger.warning(
                    "Weather enrichment failed type=%s", type(exc).__name__
                )
                enriched_context["warnings"].append("Weather service unavailable.")
                enriched_context["missing_apis"].append("weather")
        
        # Flights: fetch for itinerary generation or explicit flight request
        if is_itinerary and travel_info.get("origin") and travel_info.get("destination"):
            flight_result = await flight_task if flight_task else None

            # Normalize flight service response
            if isinstance(flight_result, list):
                flights_list = [flight.dict() if hasattr(flight, 'dict') else flight for flight in flight_result]
            elif isinstance(flight_result, dict) and "data" in flight_result:
                flight_data = flight_result["data"]
                if isinstance(flight_data, list):
                    flights_list = flight_data
                elif flight_data:
                    flights_list = [flight_data]
                else:
                    flights_list = []
            else:
                flights_list = []

            if flights_list:
                enriched_context["flights"] = flights_list
                enriched_context["flight_source"] = "live_api"
                if "flights" not in enriched_context["used_apis"]:
                    enriched_context["used_apis"].append("flights")
            else:
                # Create static fallback flights
                fallback_flights = [
                    {
                        "origin": travel_info["origin"],
                        "destination": travel_info["destination"],
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "price": "EUR 250-350",
                        "airline": "Emirates Airline",
                        "flight_number": "EK-2026"
                    },
                    {
                        "origin": travel_info["origin"],
                        "destination": travel_info["destination"],
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "price": "EUR 180-260",
                        "airline": "Lufthansa",
                        "flight_number": "LH-2031"
                    },
                    {
                        "origin": travel_info["origin"],
                        "destination": travel_info["destination"],
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "price": "EUR 120-200",
                        "airline": "Eurowings",
                        "flight_number": "EW-4512"
                    }
                ]
                enriched_context["flights"] = fallback_flights
                enriched_context["flight_source"] = "static_fallback"
                if "flights" not in enriched_context["missing_apis"]:
                    enriched_context["missing_apis"].append("flights")
                enriched_context["warnings"].append("Live flight API unavailable; showing static fallback flight options.")
        
        # Hotels: fetch for itinerary generation with destination
        if is_itinerary and travel_info.get("destination"):
            hotel_result = await hotel_task if hotel_task else None

            # Normalize hotel service response
            if isinstance(hotel_result, list):
                hotels_list = [hotel.dict() if hasattr(hotel, 'dict') else hotel for hotel in hotel_result]
            elif isinstance(hotel_result, dict) and "data" in hotel_result:
                hotel_data = hotel_result["data"]
                if isinstance(hotel_data, list):
                    hotels_list = hotel_data
                elif hotel_data:
                    hotels_list = [hotel_data]
                else:
                    hotels_list = []
            else:
                hotels_list = []

            if hotels_list:
                enriched_context["hotels"] = hotels_list
                enriched_context["hotel_source"] = "live_api"
                if "hotels" not in enriched_context["used_apis"]:
                    enriched_context["used_apis"].append("hotels")
            else:
                # Create static fallback hotels
                fallback_hotels = [
                    {
                        "name": "Hotel Europa",
                        "location": travel_info["destination"],
                        "price_per_night": "€25",
                        "currency": "EUR",
                        "pricing_basis": "per_night",
                        "source": "static_fallback",
                        "rating": 4.4
                    },
                    {
                        "name": "City Circus Hotel",
                        "location": travel_info["destination"],
                        "price_per_night": "€30",
                        "currency": "EUR",
                        "pricing_basis": "per_night",
                        "source": "static_fallback",
                        "rating": 4.3
                    },
                    {
                        "name": "Marriott International",
                        "location": travel_info["destination"],
                        "price_per_night": "€35",
                        "currency": "EUR",
                        "pricing_basis": "per_night",
                        "source": "static_fallback",
                        "rating": 4.4
                    }
                ]
                enriched_context["hotels"] = fallback_hotels
                enriched_context["hotel_source"] = "static_fallback"
                if "hotels" not in enriched_context["missing_apis"]:
                    enriched_context["missing_apis"].append("hotels")
                enriched_context["warnings"].append("Live hotel API unavailable; showing static fallback hotel options.")
        
        elif intent.get("flights", False):
            # Check if destination is missing
            if not travel_info.get("destination"):
                enriched_context["warnings"].append("Destination is missing. Please provide destination city.")
                enriched_context["missing_apis"].append("flights")
            else:
                try:
                    flights = await flight_task if flight_task else None
                    # Normalize flight service response
                    if isinstance(flights, list):
                        flight_list = [flight.dict() if hasattr(flight, 'dict') else flight for flight in flights]
                    elif isinstance(flights, dict) and "data" in flights:
                        flight_data = flights["data"]
                        if isinstance(flight_data, list):
                            flight_list = flight_data
                        elif flight_data:
                            flight_list = [flight_data]
                        else:
                            flight_list = []
                    else:
                        flight_list = []
                    
                    enriched_context["flights"] = flight_list
                    if flight_list:
                        enriched_context["used_apis"].append("flights")
                        enriched_context["flight_source"] = "live_api"
                    else:
                        enriched_context["missing_apis"].append("flights")
                        enriched_context["warnings"].append("Live flight API is not implemented.")
                    logger.info("[FLIGHT DATA] %s flights found", len(flight_list))
                except Exception as exc:
                    logger.warning(
                        "Flight enrichment failed type=%s", type(exc).__name__
                    )
                    enriched_context["warnings"].append("Flight service unavailable.")
                    enriched_context["missing_apis"].append("flights")
        
        # Hotels: fetch for itinerary or explicit hotel request
        if not is_itinerary and intent.get("hotels", False):
            try:
                hotels = await hotel_task if hotel_task else None
                if hotels:
                    enriched_context["hotels"] = [hotel.dict() if hasattr(hotel, 'dict') else hotel for hotel in hotels]
                    enriched_context["used_apis"].append("hotels")
                    enriched_context["hotel_source"] = "live_api"
                else:
                    enriched_context["warnings"].append("No hotels found or API unavailable")
                    enriched_context["missing_apis"].append("hotels")
            except Exception as exc:
                logger.warning(
                    "Hotel enrichment failed type=%s", type(exc).__name__
                )
                enriched_context["warnings"].append("Hotel service unavailable.")
                enriched_context["missing_apis"].append("hotels")
        
        # Events: fetch for itinerary or explicit events request
        if is_itinerary or intent.get("events", False):
            try:
                events = await events_task if events_task else None
                if events:
                    enriched_context["local_events"] = [event.dict() if hasattr(event, 'dict') else event for event in events]
                    enriched_context["event_source"] = "live_api"
                    enriched_context["used_apis"].append("local_events")
                else:
                    enriched_context["warnings"].append("No events found or API unavailable")
                    enriched_context["missing_apis"].append("local_events")
            except Exception as exc:
                logger.warning(
                    "Event enrichment failed type=%s", type(exc).__name__
                )
                enriched_context["warnings"].append("Event service unavailable.")
                enriched_context["missing_apis"].append("local_events")
        
        return enriched_context


# Global instance
api_context_service = ApiContextService()
