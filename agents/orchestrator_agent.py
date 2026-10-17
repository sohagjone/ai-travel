import json
from typing import Any, Dict

from .base_agent import BaseAgent
from .email_agent import EmailAgent
from .flight_agent import FlightAgent
from .hotel_agent import HotelAgent


class OrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="TravelOrchestrator",
            instructions="""Coordinate the complete travel planning workflow.
            Responsibilities:
            1. Manage flight searches for both outbound and return trips
            2. Handle hotel searches based on destination and dates
            3. Ensure proper email delivery of travel recommendations
            4. Analyze and combine results for optimal travel suggestions
            5. Track workflow progress and handle any errors
            Consider: pricing optimization, scheduling efficiency, and user preferences.
            Provide comprehensive travel recommendations with detailed pricing and booking information.""",
        )
        self._setup_agents()

    def _setup_agents(self):
        """Initialize all specialized travel agents"""
        self.flight_agent = FlightAgent()
        self.hotel_agent = HotelAgent()
        self.email_agent = EmailAgent()

    async def run(self, messages: list) -> Dict[str, Any]:
        """Process a travel planning request"""
        print("Orchestrator: Starting travel planning process")

        travel_request = self._load_payload(messages[-1]["content"])
        workflow_context = {
            "travel_request": travel_request,
            "status": "initiated",
            "current_stage": "flight_search",
        }

        try:
            # Ensure return_date is later than departure_date
            if travel_request["return_date"] <= travel_request["departure_date"]:
                raise ValueError("Return date must be later than the departure date.")

            # Search flights
            flights = await self.flight_agent.run(
                [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "origin": travel_request["origin"],
                                "destination": travel_request["destination"],
                                "departure_date": travel_request["departure_date"],
                                "return_date": travel_request["return_date"],
                                "adults": travel_request.get("adults", 1),
                                "infants_in_seat": travel_request.get("infants_in_seat", 0),
                                "infants_on_lap": travel_request.get("infants_on_lap", 0),
                                "children": travel_request.get("children", 0),
                            }
                        ),
                    }
                ]
            )
            workflow_context.update({"flights": flights, "current_stage": "hotel_search"})

            # Search hotels
            hotels = await self.hotel_agent.run(
                [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "location": travel_request["destination"],
                                "check_in": travel_request["departure_date"],
                                "check_out": travel_request["return_date"],
                                "adults": travel_request.get("adults", 1),
                                "children": travel_request.get("children", 0),
                                "rooms": travel_request.get("rooms", 1),
                                "sort_by": travel_request.get("sort_by", ""),
                                "hotel_class": travel_request.get("hotel_class", ""),
                            }
                        ),
                    }
                ]
            )
            workflow_context.update({"hotels": hotels, "current_stage": "email_preparation"})

            # Send the email only when the user asked for it
            if travel_request.get("send_email"):
                email_result = await self.email_agent.run(
                    [
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "to_email": travel_request["email"],
                                    "origin": travel_request["origin"],
                                    "destination": travel_request["destination"],
                                    "departure_date": travel_request["departure_date"],
                                    "return_date": travel_request["return_date"],
                                    "flights": flights,
                                    "hotels": hotels,
                                }
                            ),
                        }
                    ]
                )
            else:
                email_result = {
                    "success": False,
                    "skipped": True,
                    "detail": "Email not requested.",
                }
            workflow_context.update({"email_result": email_result, "status": "completed"})

            # Optional AI summary. A failure here must not lose the search results.
            workflow_context["final_recommendations"] = self._summarize(flights, hotels)
            return workflow_context

        except Exception as e:
            workflow_context.update(
                {
                    "status": "failed",
                    "error": str(e),
                    "current_stage": workflow_context.get("current_stage", "unknown"),
                }
            )
            raise

    def _summarize(self, flights: Dict[str, Any], hotels: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the AI for a short summary of the best options.
        Only a few compact options are sent, to stay inside free-tier limits."""
        top_flights = [
            {
                "airline": f.get("airline"),
                "flight_number": f.get("flight_number"),
                "price": f.get("price"),
                "duration": f.get("duration"),
            }
            for f in flights.get("recommended_flights", [])[:5]
        ]
        top_hotels = [
            {
                "name": h.get("name"),
                "rating": h.get("rating"),
                "price_per_night": h.get("price_per_night"),
                "total_price": h.get("total_price"),
            }
            for h in hotels.get("recommended_hotels", [])[:5]
        ]
        if not top_flights and not top_hotels:
            return {}

        prompt = (
            "Analyze these travel options.\n"
            f"Flights: {json.dumps(top_flights)}\n"
            f"Hotels: {json.dumps(top_hotels)}\n\n"
            "Reply with ONLY a JSON object with these keys: "
            '"best_flight" (string), "top_hotel" (string), '
            '"estimated_total_cost" (string), "summary" (2-3 sentences).'
        )
        try:
            return self._parse_json_safely(self._query_llm(prompt))
        except Exception as e:
            print(f"Summary skipped: {e}")
            return {"error": f"Summary not available: {e}"}
