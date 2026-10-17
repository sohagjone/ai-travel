import os
from datetime import datetime
from typing import Any, Dict

import serpapi

from .base_agent import BaseAgent, _get_secret


class FlightAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="FlightAgent",
            instructions="""Search and analyze flight options.
            Consider: price, duration, airline reputation, and convenience.
            Provide detailed flight information with airline logos and booking links.
            Return results in HTML format with pricing, schedules, and airline details.""",
        )
        self.serpapi_key = _get_secret("SERPAPI_API_KEY") or os.getenv("SERPAPI_API_KEY")

    async def run(self, messages: list) -> Dict[str, Any]:
        """Search and analyze flight options"""
        print("FlightAgent: Searching for flights")

        search_params = self._load_payload(messages[-1]["content"])
        flights = self.search_flights(search_params)
        return self._parse_flight_data(flights)

    def search_flights(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.serpapi_key:
            raise ValueError(
                "SERPAPI_API_KEY is missing. Add it in Streamlit Secrets "
                "(or in your local .env file)."
            )

        search_params = {
            "api_key": self.serpapi_key,
            "engine": "google_flights",
            "hl": "en",
            "gl": "us",
            "departure_id": params.get("origin", ""),
            "arrival_id": params.get("destination", ""),
            "outbound_date": params.get("departure_date", ""),
            "return_date": params.get("return_date", ""),
            "currency": "USD",
            "adults": params.get("adults", 1),
            "infants_in_seat": params.get("infants_in_seat", 0),
            "stops": "1",
            "infants_on_lap": params.get("infants_on_lap", 0),
            "children": params.get("children", 0),
        }
        try:
            search = serpapi.search(search_params)
            results = search.data
        except Exception as err:
            raise RuntimeError(f"Flight search failed: {err}") from err

        if isinstance(results, dict) and results.get("error"):
            raise RuntimeError(f"Flight search failed: {results['error']}")
        return results

    def _parse_flight_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse flight data from the API response"""
        recommended_flights = []

        def add(flight: Dict[str, Any], price: Any):
            duration = flight.get("duration") or 0
            recommended_flights.append(
                {
                    "airline": flight.get("airline"),
                    "flight_number": flight.get("flight_number"),
                    "departure": (flight.get("departure_airport") or {}).get("time"),
                    "arrival": (flight.get("arrival_airport") or {}).get("time"),
                    "price": f"${price}",
                    "duration": f"{duration // 60}h {duration % 60}m",
                    "logo_url": flight.get("airline_logo"),
                    "booking_url": (data.get("search_metadata") or {}).get(
                        "google_flights_url"
                    ),
                }
            )

        # Flights from 'other_flights'
        for flight_option in data.get("other_flights", []):
            for flight in flight_option.get("flights", []):
                add(flight, flight_option.get("price"))

        # Flights from 'best_flights'
        for flight_option in data.get("best_flights", []):
            for flight in flight_option.get("flights", []):
                add(flight, flight_option.get("price"))

        # Flights from 'flights' (older response shape)
        for flight in data.get("flights", []):
            add(flight, flight.get("price"))

        return {
            "recommended_flights": recommended_flights,
            "search_timestamp": datetime.now().strftime("%Y-%m-%d"),
            "number_of_options": len(recommended_flights),
        }
