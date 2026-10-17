import os
from datetime import datetime
from typing import Any, Dict

import requests

from .base_agent import BaseAgent, _get_secret


class CityResolverAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="CityResolverAgent",
            instructions=(
                "You identify cities from airport codes. "
                "Reply with ONLY the city name: no punctuation, no explanation."
            ),
        )

    def get_city_from_airport_code(self, airport_code: str) -> str:
        """Ask the AI model which city an airport code belongs to.
        Returns an empty string if it cannot be resolved."""
        prompt = f"Which city is served by the airport code {airport_code}?"
        try:
            answer = self._query_llm(prompt)
        except Exception as e:
            print(f"Error resolving city from airport code: {e}")
            return ""
        city = answer.strip().splitlines()[0].strip(" .\"'*")
        return city if 0 < len(city) <= 60 else ""


class HotelAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="HotelAgent",
            instructions="""Search and analyze hotel options.
            Consider: price, location, amenities, and guest ratings.
            Provide detailed hotel information with images and booking links.
            Return results in HTML format with pricing, amenities, and location details.""",
        )
        self.serpapi_key = _get_secret("SERPAPI_API_KEY") or os.getenv("SERPAPI_API_KEY")
        self.city_resolver_agent = CityResolverAgent()

    async def run(self, messages: list) -> Dict[str, Any]:
        """Search and analyze hotel options"""
        print("HotelAgent: Searching for hotels")

        search_params = self._load_payload(messages[-1]["content"])

        # Turn the airport code (e.g. AMS) into a city name for the hotel search
        code = search_params.get("location", "")
        if code:
            city = self.city_resolver_agent.get_city_from_airport_code(code)
            search_params["query"] = (
                f"hotels in {city}" if city else f"hotels near {code} airport"
            )

        hotels = self.search_hotels(search_params)
        return self._parse_hotel_data(hotels)

    def search_hotels(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.serpapi_key:
            raise ValueError(
                "SERPAPI_API_KEY is missing. Add it in Streamlit Secrets "
                "(or in your local .env file)."
            )

        search_params = {
            "api_key": self.serpapi_key,
            "engine": "google_hotels",
            "hl": "en",
            "gl": "us",
            "q": params.get("query") or f"hotels in {params.get('location', '')}",
            "check_in_date": params.get("check_in", ""),
            "check_out_date": params.get("check_out", ""),
            "currency": "USD",
            "adults": params.get("adults", 1),
            "children": params.get("children", 0),
            "rooms": params.get("rooms", 1),
            "sort_by": params.get("sort_by", ""),
            "hotel_class": params.get("hotel_class", ""),
        }
        # Don't send empty optional values to the API
        search_params = {k: v for k, v in search_params.items() if v not in ("", None)}

        try:
            response = requests.get(
                "https://serpapi.com/search", params=search_params, timeout=60
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.HTTPError as http_err:
            raise RuntimeError(f"Hotel search failed: {http_err}") from http_err
        except Exception as err:
            raise RuntimeError(f"Hotel search failed: {err}") from err

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Hotel search failed: {data['error']}")
        return data

    def _parse_hotel_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse hotel data from the API response"""
        properties = data.get("properties", [])
        recommended_hotels = []
        for hotel in properties:
            images = hotel.get("images") or [{}]
            recommended_hotels.append(
                {
                    "name": hotel.get("name"),
                    "rating": hotel.get("overall_rating"),
                    "price_per_night": (hotel.get("rate_per_night") or {}).get("lowest"),
                    "total_price": (hotel.get("total_rate") or {}).get("lowest"),
                    "location": hotel.get("description"),
                    "amenities": hotel.get("amenities"),
                    "image_url": images[0].get("thumbnail"),
                    "booking_url": hotel.get("link"),
                }
            )
        return {
            "recommended_hotels": recommended_hotels,
            "search_timestamp": datetime.now().strftime("%Y-%m-%d"),
            "number_of_options": len(recommended_hotels),
        }
