import html
import os
from datetime import datetime
from typing import Any, Dict

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from .base_agent import BaseAgent, _get_secret


def _esc(value: Any) -> str:
    """Escape text so it is safe to put inside the HTML email."""
    return html.escape("" if value is None else str(value))


class EmailAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="EmailAgent",
            instructions="""Create detailed travel itinerary emails.
            Include: flight details, hotel information, pricing, and booking links.
            Format content in clean, professional HTML with images and logos.
            Ensure all pricing and scheduling information is clearly presented.""",
            agent_type="email",
        )
        self.sendgrid_key = _get_secret("SENDGRID_KEY") or os.getenv("SENDGRID_KEY")
        # Must be a sender address you have verified in SendGrid
        self.sender_email = _get_secret("SENDER_EMAIL") or os.getenv("SENDER_EMAIL")

    async def run(self, messages: list) -> Dict[str, Any]:
        """Create and send travel itinerary email"""
        print("EmailAgent: Creating and sending itinerary")

        email_data = self._load_payload(messages[-1]["content"])
        email_content = self._generate_email_content(email_data)
        success, detail = self.send_email(email_data["to_email"], email_content)

        return {
            "success": success,
            "detail": detail,
            "content": email_content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "recipient": email_data["to_email"],
        }

    def _generate_email_content(self, email_data: Dict[str, Any]) -> str:
        """Generate HTML content for the email"""
        departure_summary = (
            f"Flights from {_esc(email_data.get('origin', '[Unknown]'))} "
            f"to {_esc(email_data.get('destination', '[Unknown]'))} "
            f"from {_esc(email_data.get('departure_date', '[Date not provided]'))} "
            f"to {_esc(email_data.get('return_date', '[Date not provided]'))}"
        )

        flights_html = (
            "<h2>Flight Options</h2><div style='display: flex; flex-wrap: wrap;'>"
        )
        for flight in email_data["flights"]["recommended_flights"]:
            logo = (
                f"<img src=\"{_esc(flight.get('logo_url'))}\" "
                f"alt=\"{_esc(flight.get('airline'))}\" "
                "style='width: 80px; height: auto;'><br>"
                if flight.get("logo_url")
                else ""
            )
            link = (
                f"<a href=\"{_esc(flight.get('booking_url'))}\" "
                "style='text-decoration: none; color: #007bff;'>Book Now</a>"
                if flight.get("booking_url")
                else ""
            )
            flights_html += f"""
            <div style='flex: 1 1 45%; margin: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 8px;'>
                <strong>{_esc(flight.get('airline'))}</strong><br>
                <strong>Flight Number:</strong> {_esc(flight.get('flight_number'))}<br>
                <strong>Departure:</strong> {_esc(flight.get('departure'))}<br>
                <strong>Arrival:</strong> {_esc(flight.get('arrival'))}<br>
                <strong>Duration:</strong> {_esc(flight.get('duration'))}<br>
                <strong>Price:</strong> {_esc(flight.get('price'))}<br>
                {logo}
                {link}
            </div>
            """
        flights_html += "</div>"

        hotels_html = (
            "<h2>Hotel Options</h2><div style='display: flex; flex-wrap: wrap;'>"
        )
        for hotel in email_data["hotels"]["recommended_hotels"]:
            rating = hotel.get("rating")
            amenities = hotel.get("amenities") or []
            image = (
                f"<img src=\"{_esc(hotel.get('image_url'))}\" "
                f"alt=\"{_esc(hotel.get('name'))}\" "
                "style='width: 150px; height: auto;'><br>"
                if hotel.get("image_url")
                else ""
            )
            link = (
                f"<a href=\"{_esc(hotel.get('booking_url'))}\" "
                "style='text-decoration: none; color: #007bff;'>Book Now</a>"
                if hotel.get("booking_url")
                else ""
            )
            hotels_html += f"""
            <div style='flex: 1 1 45%; margin: 10px; padding: 10px; border: 1px solid #ddd; border-radius: 8px;'>
                <strong>{_esc(hotel.get('name'))}</strong><br>
                <strong>Rating:</strong> {round(rating if rating is not None else 0, 1)}<br>
                <strong>Price per Night:</strong> {_esc(hotel.get('price_per_night'))}<br>
                <strong>Total Price:</strong> {_esc(hotel.get('total_price'))}<br>
                <strong>Amenities:</strong> {_esc(', '.join(amenities))}<br>
                {image}
                {link}
            </div>
            """
        hotels_html += "</div>"

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Travel Itinerary</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                h1 {{ color: #333; }}
                h2 {{ color: #0056b3; }}
            </style>
        </head>
        <body>
            <h1>Your Travel Itinerary</h1>
            <h3>{departure_summary}</h3>
            {flights_html}
            {hotels_html}
        </body>
        </html>
        """

    def send_email(self, to_email: str, content: str):
        """Send the email with SendGrid. Returns (success, message)."""
        if not self.sendgrid_key:
            return False, "SENDGRID_KEY is not set, so no email was sent."
        if not self.sender_email:
            return False, "SENDER_EMAIL is not set, so no email was sent."

        message = Mail(
            from_email=self.sender_email,
            to_emails=to_email,
            subject="Your Travel Recommendations",
            html_content=content,
        )
        try:
            sg = SendGridAPIClient(self.sendgrid_key)
            response = sg.send(message)
            print(f"Email sent with status code: {response.status_code}")
            return True, f"Email sent (status {response.status_code})."
        except Exception as e:
            print(f"Error sending email: {e}")
            return False, f"Email could not be sent: {e}"
