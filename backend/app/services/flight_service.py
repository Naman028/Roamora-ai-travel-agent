"""
Flight service using Flight API
"""
from typing import Optional, List
from backend.app.models.chat_models import FlightInfo
from backend.app.core.config import get_api_key

class FlightService:
    """Service for flight information"""
    
    def __init__(self):
        self.api_key = get_api_key("FLIGHT_API_KEY")

        if not self.api_key:
            print("Warning: FLIGHT_API_KEY not found. Flight service will be disabled.")
    
    async def search_flights(self, origin: str, destination: str, departure_date: str, 
                           return_date: Optional[str] = None) -> Optional[List[FlightInfo]]:
        """
        Search for flights between two cities
        
        Args:
            origin: Origin city/airport code
            destination: Destination city/airport code
            departure_date: Departure date (YYYY-MM-DD format)
            return_date: Return date for round-trip (optional)
            
        Returns:
            List of FlightInfo objects or None if error
        """
        if not self.api_key:
            return None
        
        # No production flight provider is implemented yet. Returning None keeps
        # static examples clearly marked as fallback data by the context service.
        return None
    
    async def get_airport_info(self, city: str) -> Optional[dict]:
        """
        Get airport information for a city
        
        Args:
            city: City name
            
        Returns:
            Airport information or None if error
        """
        # Mock implementation
        airport_codes = {
            "new york": "JFK/LGA/EWR",
            "los angeles": "LAX",
            "chicago": "ORD/MDW",
            "london": "LHR/LGW/STN",
            "paris": "CDG/ORY",
            "tokyo": "NRT/HND",
            "mumbai": "BOM",
            "delhi": "DEL"
        }
        
        city_lower = city.lower()
        if city_lower in airport_codes:
            return {
                "city": city.title(),
                "airports": airport_codes[city_lower]
            }
        
        return None

# Global flight service instance
flight_service = FlightService()
