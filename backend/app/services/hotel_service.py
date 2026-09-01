"""
Hotel service using Booking.com API
"""
import os
import httpx
from datetime import date
from typing import Optional, List
from backend.app.models.chat_models import HotelInfo
from backend.app.core.config import get_api_key

class HotelService:
    """Service for hotel information using Booking.com API"""
    
    def __init__(self):
        self.rapidapi_key = get_api_key("RAPIDAPI_KEY")
        self.rapidapi_host = os.getenv("RAPIDAPI_HOST", "booking-com15.p.rapidapi.com")
        self.base_url = f"https://{self.rapidapi_host}"
        
        if not self.rapidapi_key:
            print("Warning: RAPIDAPI_KEY not found. Hotel service will be disabled.")
    
    async def search_hotels(self, destination: str, check_in: str, 
                           check_out: str, guests: int = 1) -> Optional[List[HotelInfo]]:
        """
        Search for hotels in a destination
        
        Args:
            destination: Destination city
            check_in: Check-in date (YYYY-MM-DD)
            check_out: Check-out date (YYYY-MM-DD)
            guests: Number of guests
            
        Returns:
            List of HotelInfo objects or None if error
        """
        if not self.rapidapi_key:
            return None
        
        headers = {
            "X-RapidAPI-Key": self.rapidapi_key,
            "X-RapidAPI-Host": self.rapidapi_host
        }
        
        # First, get the destination ID
        dest_id = await self._get_destination_id(destination, headers)
        if not dest_id:
            return None
        
        # Search for hotels
        url = f"{self.base_url}/api/v1/hotels/searchHotels"
        params = {
            "dest_id": dest_id,
            "search_type": "city",
            "arrival_date": check_in,
            "departure_date": check_out,
            "adults": guests,
            "room_qty": "1",
            "page": "1",
            "currency_code": "EUR",
            "languagecode": "en-us",
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=headers, params=params)
                
                # Log rate limit headers
                rate_limit = response.headers.get('x-ratelimit-requests-limit', 'unknown')
                rate_remaining = response.headers.get('x-ratelimit-requests-remaining', 'unknown')
                print(f"Hotel API rate limit: {rate_remaining}/{rate_limit} requests remaining")
                
                response.raise_for_status()
                
                data = response.json()
                hotels = []
                
                if "data" in data and "hotels" in data["data"]:
                    nights = max((date.fromisoformat(check_out) - date.fromisoformat(check_in)).days, 1)
                    for hotel_data in data["data"]["hotels"][:10]:  # Limit to 10 results
                        price = self._extract_price(hotel_data, nights)
                        hotel = HotelInfo(
                            name=hotel_data.get("property", {}).get("name", "Unknown Hotel"),
                            location=destination,
                            price_per_night=price["price_per_night"],
                            price_total=price["price_total"],
                            currency=price["currency"],
                            pricing_basis="total_stay",
                            rating=hotel_data.get("property", {}).get("reviewScore"),
                            amenities=self._extract_amenities(hotel_data)
                        )
                        hotels.append(hotel)
                
                return hotels
                
        except httpx.HTTPStatusError as e:
            print(f"Hotel API HTTP error: {e}")
            return None
        except Exception as e:
            print(f"Hotel API error: {e}")
            return None
    
    async def _get_destination_id(self, destination: str, headers: dict) -> Optional[str]:
        """Get destination ID for Booking.com API"""
        url = f"{self.base_url}/api/v1/hotels/searchDestination"
        params = {"query": destination}
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers, params=params)
                
                # Log rate limit headers
                rate_limit = response.headers.get('x-ratelimit-requests-limit', 'unknown')
                rate_remaining = response.headers.get('x-ratelimit-requests-remaining', 'unknown')
                print(f"Hotel destination API rate limit: {rate_remaining}/{rate_limit} requests remaining")
                
                response.raise_for_status()
                
                data = response.json()
                if "data" in data and len(data["data"]) > 0:
                    return data["data"][0].get("dest_id")
                
                return None
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = e.response.headers.get('retry-after', 'unknown')
                print(f"Hotel API rate limit hit. Retry after: {retry_after} seconds")
                print(f"URL: {e.request.url}")
            else:
                print(f"Hotel API HTTP error: {e}")
            return None
        except httpx.HTTPError as e:
            print(f"Hotel API connection error: {e}")
            return None
    
    def _extract_price(self, hotel_data: dict, nights: int) -> dict:
        """Extract the API's total-stay price and derive a display-only nightly price."""
        try:
            price_breakdown = (
                hotel_data.get("property", {}).get("priceBreakdown")
                or hotel_data.get("priceBreakdown")
                or {}
            )
            gross_price = price_breakdown.get("grossPrice", {})
            value = float(gross_price["value"])
            currency = str(gross_price.get("currency") or "EUR").upper()
            return {
                "price_per_night": round(value / max(nights, 1), 2),
                "price_total": round(value, 2),
                "currency": currency,
            }
        except (KeyError, TypeError, ValueError):
            return {"price_per_night": None, "price_total": None, "currency": None}
    
    def _extract_amenities(self, hotel_data: dict) -> List[str]:
        """Extract amenities from hotel data"""
        try:
            property_info = hotel_data.get("property", {})
            amenities = property_info.get("importantFacilities", [])
            return amenities[:5]  # Limit to first 5 amenities
        except (AttributeError, TypeError):
            return []

# Global hotel service instance
hotel_service = HotelService()
