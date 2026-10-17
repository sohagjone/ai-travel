import asyncio
import json
import os
from datetime import date, timedelta

import streamlit as st
from dotenv import load_dotenv

st.set_page_config(page_title="AI Travel Agents", page_icon="✈️", layout="centered")

# Local development: read keys from a .env file (ignored by Git)
load_dotenv()


def load_secrets_into_env():
    """On Streamlit Cloud, copy the keys from Secrets into environment variables
    so every agent can read them with os.getenv()."""
    try:
        for key in st.secrets:
            value = st.secrets[key]
            if isinstance(value, str):
                os.environ.setdefault(key, value)
    except Exception:
        pass  # no secrets configured (e.g. running locally with .env)


load_secrets_into_env()

# Imported after the keys are loaded
from agents.orchestrator_agent import OrchestratorAgent  # noqa: E402


async def process_travel_request(travel_request):
    orchestrator = OrchestratorAgent()

    # Ensure return_date is later than departure_date
    if travel_request["return_date"] <= travel_request["departure_date"]:
        raise ValueError("Return date must be later than the departure date.")

    return await orchestrator.run(
        [{"role": "user", "content": json.dumps(travel_request)}]
    )


def show_setup_status():
    """Show which keys are configured (never shows the keys themselves)."""
    with st.sidebar:
        st.header("Setup status")
        has_ai = any(
            os.getenv(name) for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY")
        )
        st.write(("✅" if has_ai else "❌") + " AI key (Gemini or Groq)")
        st.write(("✅" if os.getenv("SERPAPI_API_KEY") else "❌") + " SerpAPI key")
        email_ready = bool(os.getenv("SENDGRID_KEY") and os.getenv("SENDER_EMAIL"))
        st.write(("✅" if email_ready else "➖") + " Email (optional)")
        if not has_ai or not os.getenv("SERPAPI_API_KEY"):
            st.caption("Add the missing keys in Manage app → Settings → Secrets.")


def show_flights(results):
    flights = results["flights"]["recommended_flights"]
    if not flights:
        st.info("No flights were found for these dates and airports.")
        return
    for idx, flight in enumerate(flights, start=1):
        st.markdown(f"### Option {idx}: {flight['airline']}")
        col1, col2 = st.columns([1, 3])
        with col1:
            if flight.get("logo_url"):
                st.image(flight["logo_url"], width=100)
        with col2:
            st.markdown(f"- **Airline:** {flight['airline']}")
            st.markdown(f"- **Flight Number:** {flight['flight_number']}")
            st.markdown(f"- **Departure:** {flight['departure']}")
            st.markdown(f"- **Arrival:** {flight['arrival']}")
            st.markdown(f"- **Duration:** {flight['duration']}")
            st.markdown(f"- **Price:** {flight['price']}")
            if flight.get("booking_url"):
                st.markdown(f"[Book Now]({flight['booking_url']})")


def show_hotels(results):
    hotels = results["hotels"]["recommended_hotels"]
    if not hotels:
        st.info("No hotels were found for these dates.")
        return
    for idx, hotel in enumerate(hotels, start=1):
        st.markdown(f"### Option {idx}: {hotel['name']}")
        col1, col2 = st.columns([1, 3])
        with col1:
            if hotel.get("image_url"):
                st.image(hotel["image_url"], width=200)
        with col2:
            rating = hotel["rating"] if hotel["rating"] is not None else 0
            st.markdown(f"- **Rating:** {round(rating, 1)}")
            st.markdown(f"- **Price per Night:** {hotel['price_per_night']}")
            st.markdown(f"- **Total Price:** {hotel['total_price']}")
            amenities = hotel.get("amenities")
            amenities_str = (
                ", ".join(amenities) if isinstance(amenities, list) and amenities
                else "No amenities listed"
            )
            st.markdown(f"- **Amenities:** {amenities_str}")
            if hotel.get("booking_url"):
                st.markdown(f"[Book Now]({hotel['booking_url']})")


def main():
    st.title("Travel Information Request")
    show_setup_status()

    st.subheader("Fill out the following details:")
    origin = st.text_input("Origin Airport Code (e.g., MAD)").strip().upper()
    destination = st.text_input("Destination Airport Code (e.g., AMS)").strip().upper()
    departure_date = st.date_input("Departure Date", value=date.today() + timedelta(days=30))
    return_date = st.date_input("Return Date", value=date.today() + timedelta(days=37))
    email = st.text_input("Your Email Address")
    adults = st.number_input("Number of Adults", min_value=1, step=1)
    children = st.number_input("Number of Children", min_value=0, step=1)
    rooms = st.number_input("Number of Rooms", min_value=1, step=1)

    send_email = st.radio(
        "Do you want to send this information via email?", ("No", "Yes")
    )

    if st.button("Get Travel Information"):
        if not origin or not destination:
            st.error("Please enter both airport codes.")
            return
        if send_email == "Yes" and "@" not in email:
            st.error("Please enter a valid email address to receive the email.")
            return

        departure_summary = (
            f"Flights from {origin} to {destination} from {departure_date} to {return_date}"
        )
        st.markdown(f"## {departure_summary}")

        travel_request = {
            "origin": origin,
            "destination": destination,
            "departure_date": str(departure_date),
            "return_date": str(return_date),
            "email": email,
            "adults": int(adults),
            "children": int(children),
            "rooms": int(rooms),
            "send_email": send_email == "Yes",
        }

        try:
            with st.spinner("Searching flights and hotels..."):
                results = asyncio.run(process_travel_request(travel_request))
            st.success("Travel information processed successfully!")

            summary = (results.get("final_recommendations") or {}).get("summary")
            if summary:
                st.info(summary)

            if "flights" in results and "hotels" in results:
                tab1, tab2 = st.tabs(["Flight Options", "Hotel Options"])
                with tab1:
                    show_flights(results)
                with tab2:
                    show_hotels(results)

            if send_email == "Yes":
                email_result = results.get("email_result", {})
                if email_result.get("success"):
                    st.success(f"Email sent to {email_result.get('recipient')}.")
                else:
                    st.warning(email_result.get("detail", "The email was not sent."))
        except ValueError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
