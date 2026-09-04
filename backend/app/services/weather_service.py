"""
Weather service using OpenWeatherMap API
"""
import logging

import httpx
from typing import Optional
from backend.app.core.observability import log_operation_failure
from backend.app.models.chat_models import WeatherInfo
from backend.app.core.config import get_api_key

logger = logging.getLogger(__name__)

class WeatherService:
    """Service for weather information"""
    
    def __init__(self):
        self.api_key = get_api_key("OPENWEATHERMAP_API_KEY")
        self.base_url = "https://api.openweathermap.org/data/2.5"
        
        if not self.api_key:
            logger.warning(
                "service_disabled operation=weather reason=missing_api_key"
            )
    
    async def get_current_weather(self, city: str) -> Optional[WeatherInfo]:
        """
        Get current weather for a city
        
        Args:
            city: Name of the city
            
        Returns:
            WeatherInfo object or None if error
        """
        if not self.api_key:
            return None
        
        url = f"{self.base_url}/weather"
        params = {
            "q": city,
            "appid": self.api_key,
            "units": "metric"  # Use Celsius
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                
                response.raise_for_status()
                
                data = response.json()
                
                return WeatherInfo(
                    location=data["name"],
                    temperature=data["main"]["temp"],
                    description=data["weather"][0]["description"].title(),
                    humidity=data["main"]["humidity"],
                    wind_speed=data["wind"]["speed"]
                )
                
        except httpx.HTTPStatusError as exc:
            log_operation_failure(
                logger, operation="weather_current_request", error=exc
            )
            return None
        except Exception as exc:
            log_operation_failure(
                logger, operation="weather_current_parse", error=exc
            )
            return None
    
    async def get_weather_forecast(self, city: str, days: int = 5) -> Optional[list]:
        """
        Get weather forecast for a city
        
        Args:
            city: Name of the city
            days: Number of days to forecast (max 5)
            
        Returns:
            List of forecast data or None if error
        """
        if not self.api_key:
            return None
        
        url = f"{self.base_url}/forecast"
        params = {
            "q": city,
            "appid": self.api_key,
            "units": "metric",
            "cnt": min(days * 8, 40)  # 8 forecasts per day, max 40
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                
                data = response.json()
                
                forecasts = []
                for item in data["list"][:days * 8]:  # Limit to requested days
                    forecasts.append({
                        "datetime": item["dt_txt"],
                        "temperature": item["main"]["temp"],
                        "description": item["weather"][0]["description"].title(),
                        "humidity": item["main"]["humidity"],
                        "wind_speed": item["wind"]["speed"]
                    })
                
                return forecasts
                
        except httpx.HTTPStatusError as exc:
            log_operation_failure(
                logger, operation="weather_forecast_request", error=exc
            )
            return None
        except Exception as exc:
            log_operation_failure(
                logger, operation="weather_forecast_parse", error=exc
            )
            return None

# Global weather service instance
weather_service = WeatherService()
