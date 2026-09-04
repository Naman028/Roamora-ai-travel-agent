"""
Events service using Ticketmaster API
"""
import os
import httpx
import logging
from typing import Optional, List
from backend.app.models.chat_models import EventInfo
from backend.app.core.config import get_api_key
from backend.app.core.observability import log_operation_failure

logger = logging.getLogger(__name__)

class EventsService:
    """Service for local events using Ticketmaster API"""
    
    def __init__(self):
        self.api_key = get_api_key("TICKETMASTER_API_KEY")
        self.base_url = "https://app.ticketmaster.com/discovery/v2"
        
        if not self.api_key:
            logger.warning(
                "service_disabled operation=events reason=missing_api_key"
            )
    
    async def search_events(self, city: str, start_date: Optional[str] = None, 
                           end_date: Optional[str] = None, category: Optional[str] = None) -> Optional[List[EventInfo]]:
        """
        Search for events in a city
        
        Args:
            city: City name
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            category: Event category (music, sports, arts, etc.)
            
        Returns:
            List of EventInfo objects or None if error
        """
        if not self.api_key:
            return None
        
        url = f"{self.base_url}/events.json"
        params = {
            "apikey": self.api_key,
            "city": city,
            "size": 10,  # Limit to 10 results as required
            "sort": "date,asc"
        }
        
        # Add date filters if provided
        if start_date:
            params["startDateTime"] = f"{start_date}T00:00:00Z"
        if end_date:
            params["endDateTime"] = f"{end_date}T23:59:59Z"
        
        # Add category filter using segmentName as required
        if category:
            category_map = {
                "music": "Music",
                "sports": "Sports", 
                "arts": "Arts & Theatre",
                "theatre": "Arts & Theatre",
                "family": "Family"
            }
            if category.lower() in category_map:
                params["segmentName"] = category_map[category.lower()]
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params)
                
                response.raise_for_status()
                
                data = response.json()
                
                events = []
                
                if "_embedded" in data and "events" in data["_embedded"]:
                    for event_data in data["_embedded"]["events"]:
                        event = EventInfo(
                            name=event_data.get("name", "Unknown Event"),
                            date=self._extract_event_date(event_data),
                            location=self._extract_event_location(event_data),
                            category=self._extract_event_category(event_data),
                            price=self._extract_event_price(event_data),
                            url=self._extract_event_url(event_data)
                        )
                        events.append(event)
                logger.info(
                    "provider_result operation=events result_count=%s", len(events)
                )
                
                return events
                
        except httpx.HTTPStatusError as exc:
            log_operation_failure(
                logger, operation="events_search_request", error=exc
            )
            return None
        except Exception as exc:
            log_operation_failure(
                logger, operation="events_search_parse", error=exc
            )
            return None
    
    def _extract_event_date(self, event_data: dict) -> str:
        """Extract event date and time from event data"""
        try:
            dates = event_data.get("dates", {})
            start = dates.get("start", {})
            date = start.get("localDate", "Date not available")
            time = start.get("localTime", "")
            
            if date and time:
                return f"{date} at {time}"
            elif date:
                return date
            else:
                return "Date not available"
        except Exception as exc:
            log_operation_failure(
                logger, operation="event_date_extract", error=exc
            )
            return "Date not available"
    
    def _extract_event_location(self, event_data: dict) -> str:
        """Extract event location from event data"""
        try:
            if "_embedded" in event_data and "venues" in event_data["_embedded"]:
                venue = event_data["_embedded"]["venues"][0]
                name = venue.get("name", "")
                city = venue.get("city", {}).get("name", "")
                state = venue.get("state", {}).get("name", "")
                country = venue.get("country", {}).get("name", "")
                
                location_parts = []
                if name:
                    location_parts.append(name)
                if city:
                    location_parts.append(city)
                if state:
                    location_parts.append(state)
                if country:
                    location_parts.append(country)
                
                return ", ".join(location_parts) if location_parts else "Location not available"
        except Exception as exc:
            log_operation_failure(
                logger, operation="event_location_extract", error=exc
            )
            return "Location not available"
    
    def _extract_event_category(self, event_data: dict) -> str:
        """Extract event category from event data"""
        try:
            if "classifications" in event_data and len(event_data["classifications"]) > 0:
                classification = event_data["classifications"][0]
                segment = classification.get("segment", {}).get("name", "")
                genre = classification.get("genre", {}).get("name", "")
                
                if segment and genre:
                    return f"{segment} - {genre}"
                elif segment:
                    return segment
                elif genre:
                    return genre
        except Exception as exc:
            log_operation_failure(
                logger, operation="event_category_extract", error=exc
            )
            return "General"
    
    def _extract_event_price(self, event_data: dict) -> Optional[str]:
        """Extract event price from event data"""
        try:
            price_ranges = event_data.get("priceRanges", [])
            if price_ranges:
                price = price_ranges[0]
                min_price = price.get("min")
                max_price = price.get("max")
                currency = price.get("currency", "USD")
                
                if min_price and max_price:
                    return f"{currency} {min_price}-{max_price}"
                elif min_price:
                    return f"{currency} {min_price}+"
        except Exception as exc:
            log_operation_failure(
                logger, operation="event_price_extract", error=exc
            )
            return None
    
    def _extract_event_url(self, event_data: dict) -> str:
        """Extract event URL from event data"""
        try:
            return event_data.get("url", "")
        except Exception as exc:
            log_operation_failure(
                logger, operation="event_url_extract", error=exc
            )
            return ""

# Global events service instance
events_service = EventsService()
