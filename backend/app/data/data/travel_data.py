"""
Travel-related data and reference information
"""

# Popular destinations with their characteristics
POPULAR_DESTINATIONS = {
    "paris": {
        "country": "France",
        "type": "cultural",
        "best_months": ["April", "May", "September", "October"],
        "average_cost": "$200-400/day",
        "highlights": ["Eiffel Tower", "Louvre Museum", "Notre-Dame", "Champs-Élysées"],
        "activities": ["museums", "fine dining", "shopping", "river cruises"]
    },
    "bali": {
        "country": "Indonesia",
        "type": "beach",
        "best_months": ["April", "May", "June", "July", "August", "September"],
        "average_cost": "$50-150/day",
        "highlights": ["Beaches", "Temples", "Rice Terraces", "Ubud"],
        "activities": ["surfing", "yoga", "temple visits", "spa treatments"]
    },
    "new york": {
        "country": "USA",
        "type": "city",
        "best_months": ["April", "May", "June", "September", "October", "November"],
        "average_cost": "$250-500/day",
        "highlights": ["Times Square", "Central Park", "Statue of Liberty", "Broadway"],
        "activities": ["Broadway shows", "museums", "shopping", "fine dining"]
    },
    "tokyo": {
        "country": "Japan",
        "type": "cultural",
        "best_months": ["March", "April", "May", "October", "November"],
        "average_cost": "$150-350/day",
        "highlights": ["Mount Fuji", "Senso-ji Temple", "Shibuya Crossing", "Tokyo Tower"],
        "activities": ["temples", "anime culture", "sushi", "technology"]
    },
    "dubai": {
        "country": "UAE",
        "type": "city",
        "best_months": ["November", "December", "January", "February", "March"],
        "average_cost": "$200-400/day",
        "highlights": ["Burj Khalifa", "Dubai Mall", "Palm Jumeirah", "Desert Safari"],
        "activities": ["shopping", "desert safari", "skydiving", "luxury experiences"]
    }
}

# Travel tips by category
TRAVEL_TIPS = {
    "safety": [
        "Keep copies of important documents (passport, visa, insurance)",
        "Research local emergency numbers and embassy locations",
        "Use reputable transportation services",
        "Avoid displaying expensive items in public",
        "Stay aware of your surroundings, especially in crowded areas"
    ],
    "budget": [
        "Travel during off-peak seasons for better prices",
        "Book flights and accommodations in advance",
        "Use public transportation when possible",
        "Eat at local restaurants instead of tourist traps",
        "Consider travel insurance for unexpected expenses"
    ],
    "packing": [
        "Check weather forecast before packing",
        "Roll clothes instead of folding to save space",
        "Pack essentials in carry-on bag",
        "Bring universal power adapter",
        "Include basic first-aid supplies"
    ],
    "cultural": [
        "Learn basic phrases in the local language",
        "Research local customs and etiquette",
        "Dress appropriately for religious sites",
        "Be mindful of photography restrictions",
        "Try local cuisine and respect dining etiquette"
    ]
}

# Airport codes mapping
AIRPORT_CODES = {
    "new york": ["JFK", "LGA", "EWR"],
    "los angeles": ["LAX"],
    "chicago": ["ORD", "MDW"],
    "london": ["LHR", "LGW", "STN"],
    "paris": ["CDG", "ORY"],
    "tokyo": ["NRT", "HND"],
    "dubai": ["DXB"],
    "singapore": ["SIN"],
    "mumbai": ["BOM"],
    "delhi": ["DEL"],
    "bangkok": ["BKK"],
    "sydney": ["SYD"],
    "toronto": ["YYZ"],
    "vancouver": ["YVR"]
}

# Currency exchange rates (approximate, should be updated from API)
CURRENCY_RATES = {
    "USD": 1.0,
    "EUR": 0.85,
    "GBP": 0.73,
    "JPY": 110.0,
    "AUD": 1.35,
    "CAD": 1.25,
    "CHF": 0.92,
    "CNY": 6.45,
    "INR": 74.0,
    "AED": 3.67
}

# Common travel phrases
TRAVEL_PHRASES = {
    "spanish": {
        "hello": "Hola",
        "thank you": "Gracias",
        "excuse me": "Perdón",
        "how much": "¿Cuánto cuesta?",
        "where is": "¿Dónde está?"
    },
    "french": {
        "hello": "Bonjour",
        "thank you": "Merci",
        "excuse me": "Excusez-moi",
        "how much": "Combien ça coûte?",
        "where is": "Où est?"
    },
    "italian": {
        "hello": "Ciao",
        "thank you": "Grazie",
        "excuse me": "Scusi",
        "how much": "Quanto costa?",
        "where is": "Dov'è?"
    },
    "german": {
        "hello": "Hallo",
        "thank you": "Danke",
        "excuse me": "Entschuldigung",
        "how much": "Wie viel kostet das?",
        "where is": "Wo ist?"
    },
    "japanese": {
        "hello": "Konnichiwa",
        "thank you": "Arigatou",
        "excuse me": "Sumimasen",
        "how much": "Ikura desu ka?",
        "where is": "Doko desu ka?"
    }
}

# Visa requirements by country (simplified)
VISA_REQUIREMENTS = {
    "usa": {
        "visa_required_for": ["Most countries need visa"],
        "visa_free_for": ["Canada", "Mexico", "Most European countries"],
        "processing_time": "2-4 weeks"
    },
    "europe": {
        "visa_required_for": ["Most non-EU countries need Schengen visa"],
        "visa_free_for": ["EU citizens", "USA", "Canada", "Australia"],
        "processing_time": "2-3 weeks"
    },
    "japan": {
        "visa_required_for": ["Most countries need visa"],
        "visa_free_for": ["USA", "UK", "Canada", "Australia", "Most EU countries"],
        "processing_time": "1-2 weeks"
    }
}

# Emergency contact numbers
EMERGENCY_NUMBERS = {
    "usa": "911",
    "europe": "112",
    "uk": "999",
    "japan": "110",
    "australia": "000",
    "india": "112",
    "china": "110",
    "brazil": "190",
    "mexico": "911"
}
