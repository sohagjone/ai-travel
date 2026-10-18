"""Small helpers for the on-screen results (formatting, sorting, highlights). No Streamlit here."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

FLIGHT_SORTS = {
    "Price (low to high)": lambda f: (f.get("price") is None, f.get("price") or 0),
    "Duration (shortest)": lambda f: (f.get("duration_minutes") is None, f.get("duration_minutes") or 0),
    "Departure (earliest)": lambda f: (not f.get("departure"), f.get("departure") or ""),
}
HOTEL_SORTS = {
    "Rating (highest)": lambda h: (h.get("rating") is None, -(h.get("rating") or 0)),
    "Price per night (low to high)": lambda h: (h.get("price_per_night") is None, h.get("price_per_night") or 0),
    "Most reviews": lambda h: (h.get("reviews") is None, -(h.get("reviews") or 0)),
}


def _parse(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def fmt_time(value: Optional[str]) -> str:
    dt = _parse(value)
    return dt.strftime("%H:%M") if dt else "n/a"


def fmt_date(value: Optional[str]) -> str:
    dt = _parse(value)
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}" if dt else ""


def day_offset(departure: Optional[str], arrival: Optional[str]) -> str:
    """'+1' when the flight lands on a later calendar day."""
    d, a = _parse(departure), _parse(arrival)
    if d and a and a.date() > d.date():
        return f"+{(a.date() - d.date()).days}"
    return ""


def stops_text(stops: Optional[int]) -> str:
    if stops is None:
        return "Stops n/a"
    return "Nonstop" if stops == 0 else f"{stops} stop" + ("s" if stops > 1 else "")


def md_link(url: str, label: str) -> str:
    """Markdown link that survives parentheses/spaces in URLs (Streamlit opens it in a new tab)."""
    return f"[{label}]({url.replace('(', '%28').replace(')', '%29').replace(' ', '%20')})"


def flight_highlights(flights: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
    priced = [f for f in flights if f.get("price") is not None]
    timed = [f for f in flights if f.get("duration_minutes")]
    return {
        "cheapest": min(priced, key=lambda f: f["price"], default=None),
        "fastest": min(timed, key=lambda f: f["duration_minutes"], default=None),
    }


def hotel_highlights(hotels: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
    rated = [h for h in hotels if h.get("rating") is not None]
    priced = [h for h in hotels if h.get("price_per_night") is not None]
    return {
        "top_rated": max(rated, key=lambda h: (h["rating"], h.get("reviews") or 0), default=None),
        "lowest_price": min(priced, key=lambda h: h["price_per_night"], default=None),
    }
