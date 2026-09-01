"""
Travel itinerary generation service
"""
from typing import List, Dict, Any
from backend.app.models.chat_models import TravelItinerary
from backend.app.services.weather_service import weather_service
from backend.app.services.events_service import events_service
import re

class ItineraryService:
    """Service for generating travel itineraries"""
    
    def __init__(self):
        self.activity_templates = {
            "beach": ["swimming", "sunbathing", "beach volleyball", "surfing", "snorkeling"],
            "city": ["city tours", "museums", "shopping", "local markets", "historical sites"],
            "mountain": ["hiking", "nature walks", "photography", "wildlife watching", "camping"],
            "cultural": ["temples", "historical monuments", "cultural shows", "local festivals", "art galleries"],
            "adventure": ["rock climbing", "zip-lining", "water sports", "bungee jumping", "paragliding"]
        }
    
    async def generate_itinerary(self, destination: str, duration_days: int, 
                               preferences: List[str] = None) -> TravelItinerary:
        """
        Generate a travel itinerary for a destination
        
        Args:
            destination: Travel destination
            duration_days: Number of days
            preferences: List of traveler preferences
            
        Returns:
            TravelItinerary object
        """
        if preferences is None:
            preferences = []
        
        # Determine destination type and activities
        destination_type = self._classify_destination(destination)
        activities = self._generate_activities(destination_type, duration_days, preferences)
        
        # Get weather information
        weather_info = await weather_service.get_current_weather(destination)
        
        # Get local events
        events = await events_service.search_events(destination)
        
        # Generate accommodation suggestions
        accommodation = self._suggest_accommodation(destination_type, duration_days)
        
        # Generate transportation suggestions
        transportation = self._suggest_transportation(destination_type)
        
        # Estimate budget
        estimated_budget = self._estimate_budget(destination_type, duration_days)
        
        # Add weather and events context to activities
        if weather_info:
            activities.insert(0, f"Check weather: {weather_info.temperature}°C, {weather_info.description}")
        
        if events and len(events) > 0:
            activities.append(f"Consider local events: {events[0].name} on {events[0].date}")
        
        return TravelItinerary(
            destination=destination,
            duration_days=duration_days,
            activities=activities,
            accommodation=accommodation,
            transportation=transportation,
            estimated_budget=estimated_budget
        )
    
    def _classify_destination(self, destination: str) -> str:
        """Classify destination type based on name"""
        destination_lower = destination.lower()
        
        beach_keywords = ["beach", "coast", "shore", "island", "maldives", "bali", "hawaii", "miami"]
        mountain_keywords = ["mountain", "alps", "himalaya", "rocky", "ski", "hiking"]
        city_keywords = ["city", "new york", "london", "paris", "tokyo", "dubai", "singapore"]
        cultural_keywords = ["temple", "ancient", "historical", "rome", "egypt", "athens", "kyoto"]
        
        if any(keyword in destination_lower for keyword in beach_keywords):
            return "beach"
        elif any(keyword in destination_lower for keyword in mountain_keywords):
            return "mountain"
        elif any(keyword in destination_lower for keyword in cultural_keywords):
            return "cultural"
        elif any(keyword in destination_lower for keyword in city_keywords):
            return "city"
        else:
            return "city"  # Default to city
    
    def _generate_activities(self, destination_type: str, duration_days: int, 
                           preferences: List[str]) -> List[str]:
        """Generate activities based on destination type and preferences"""
        base_activities = self.activity_templates.get(destination_type, self.activity_templates["city"])
        
        # Add preference-based activities
        if preferences:
            for pref in preferences:
                pref_lower = pref.lower()
                if pref_lower in self.activity_templates:
                    base_activities.extend(self.activity_templates[pref_lower])
        
        # Create day-wise activities
        activities = []
        for day in range(1, min(duration_days + 1, 8)):  # Limit to 7 days max
            if day <= len(base_activities):
                activities.append(f"Day {day}: {base_activities[day - 1]}")
            else:
                activities.append(f"Day {day}: Explore local attractions and cuisine")
        
        # Add general travel tips
        activities.extend([
            "Try local cuisine and restaurants",
            "Visit popular tourist attractions",
            "Interact with locals for authentic experience",
            "Keep some free time for spontaneous activities"
        ])
        
        return activities[:10]  # Limit to 10 activities
    
    def _suggest_accommodation(self, destination_type: str, duration_days: int) -> str:
        """Suggest accommodation based on destination type"""
        suggestions = {
            "beach": "Beachfront resort or hotel with ocean views",
            "mountain": "Mountain lodge or cabin with nature views",
            "city": "Downtown hotel near major attractions",
            "cultural": "Heritage hotel or boutique accommodation",
            "adventure": "Eco-lodge or adventure resort"
        }
        
        base_suggestion = suggestions.get(destination_type, "Hotel in city center")
        
        if duration_days > 7:
            base_suggestion += " (consider extended stay discounts)"
        
        return base_suggestion
    
    def _suggest_transportation(self, destination_type: str) -> str:
        """Suggest transportation based on destination type"""
        suggestions = {
            "beach": "Rental car or taxi for beach hopping",
            "mountain": "4x4 vehicle or organized tours",
            "city": "Public transport or walking for city exploration",
            "cultural": "Guided tours or local transport",
            "adventure": "Specialized adventure transport equipment"
        }
        
        return suggestions.get(destination_type, "Local transportation options")
    
    def _estimate_budget(self, destination_type: str, duration_days: int) -> str:
        """Estimate budget based on destination type and duration"""
        daily_costs = {
            "beach": "$150-300",
            "mountain": "$100-250",
            "city": "$200-400",
            "cultural": "$120-280",
            "adventure": "$250-500"
        }
        
        daily_cost = daily_costs.get(destination_type, "$150-350")
        
        if duration_days == 1:
            return f"{daily_cost} per day"
        else:
            min_budget = int(daily_cost.split("-")[0].replace("$", "")) * duration_days
            max_budget = int(daily_cost.split("-")[1].replace("$", "")) * duration_days
            return f"${min_budget}-${max_budget} for {duration_days} days"

# Global itinerary service instance
itinerary_service = ItineraryService()
