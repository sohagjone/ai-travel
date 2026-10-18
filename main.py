import asyncio
import json
import re
import uuid
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agents.email_agent import EmailAgent
from agents.orchestrator_agent import OrchestratorAgent
from utils.config import get_secret, redact
from utils.export_reports import build_all_exports
from utils.report_data import (
    FLIGHT_COLUMNS,
    STOPS_LABELS,
    build_report,
    currency_symbol,
    fmt_money,
)
from utils.validation import (
    airport_info,
    city_for_airport,
    is_valid_email,
    normalise_airport_code,
    validate_travel_request,
)

load_dotenv()

# Every input widget has a key so "New search" can reset the form reliably.
FORM_KEYS = ["origin", "destination", "dep_date", "ret_date", "adults", "children", "rooms", "stops", "hotel_city", "email_to"]


# --------------------------------------------------------------------------
# Search (kept separate from exporting: downloads never call the APIs again)
# --------------------------------------------------------------------------
async def process_travel_request(travel_request: Dict[str, Any]) -> Dict[str, Any]:
    """Run the orchestrator for one validated request."""
    if travel_request["return_date"] <= travel_request["departure_date"]:
        raise ValueError("Return date must be later than the departure date.")
    orchestrator = OrchestratorAgent()
    return await orchestrator.run([{"role": "user", "content": json.dumps(travel_request)}])


def _md_safe(text: Any) -> str:
    """Escape Markdown (and `$`, which Streamlit would treat as LaTeX) in provider text."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>$~])", r"\\\1", "" if text is None else str(text))


def _download_button(label: str, data: bytes, file_name: str, mime: str, key: str) -> None:
    """st.download_button that does not trigger a rerun when supported (Streamlit >= 1.43)."""
    try:
        st.download_button(label, data=data, file_name=file_name, mime=mime, key=key, on_click="ignore", use_container_width=True)
    except TypeError:
        st.download_button(label, data=data, file_name=file_name, mime=mime, key=key, use_container_width=True)


def reset_search() -> None:
    """Callback for the 'New search' button: forget results and restore form defaults."""
    st.session_state.pop("search", None)
    for key in FORM_KEYS + [k for k in list(st.session_state) if k.startswith("child_age_")]:
        st.session_state.pop(key, None)


def _form_signature(values: Dict[str, Any]) -> tuple:
    return tuple(json.dumps(values[k], default=str, sort_keys=True) for k in sorted(values))


# --------------------------------------------------------------------------
# UI pieces
# --------------------------------------------------------------------------
def render_form() -> Optional[Dict[str, Any]]:
    """Draw the search form. Returns the form values (and a `submit` flag)."""
    today = date.today()
    st.subheader("Plan your trip")
    c1, c2 = st.columns(2)
    origin = c1.text_input("Origin airport code", key="origin", placeholder="e.g. MAD", max_chars=3, help="3-letter IATA code")
    destination = c2.text_input("Destination airport code", key="destination", placeholder="e.g. AMS", max_chars=3, help="3-letter IATA code")
    d1, d2 = st.columns(2)
    departure_date = d1.date_input("Departure date", value=today + timedelta(days=30), min_value=today, key="dep_date")
    return_date = d2.date_input("Return date", value=today + timedelta(days=37), min_value=today, key="ret_date")
    t1, t2, t3 = st.columns(3)
    adults = int(t1.number_input("Adults", min_value=1, max_value=9, value=1, step=1, key="adults"))
    children = int(t2.number_input("Children", min_value=0, max_value=8, value=0, step=1, key="children"))
    rooms = int(t3.number_input("Rooms", min_value=1, max_value=8, value=1, step=1, key="rooms"))

    children_ages = []
    if children:
        st.caption("Children's ages are needed for hotel prices.")
        age_cols = st.columns(min(children, 4))
        for i in range(children):
            with age_cols[i % len(age_cols)]:
                children_ages.append(int(st.number_input(f"Child {i + 1} age", min_value=0, max_value=17, value=8, step=1, key=f"child_age_{i}")))

    with st.expander("More options"):
        stops = st.selectbox(
            "Flight stops",
            options=list(STOPS_LABELS.keys()),
            index=1,
            format_func=lambda k: STOPS_LABELS[k],
            key="stops",
            help="The original app searched nonstop flights only, so that is still the default.",
        )
        hotel_city = st.text_input("Hotel city (optional)", key="hotel_city", help="Leave empty to use the destination airport's city.")

    submitted = st.button("Search flights and hotels", type="primary")
    return {
        "submit": submitted,
        "origin": normalise_airport_code(origin),
        "destination": normalise_airport_code(destination),
        "departure_date": departure_date,
        "return_date": return_date,
        "adults": adults,
        "children": children,
        "children_ages": children_ages,
        "rooms": rooms,
        "stops": stops,
        "hotel_city": (hotel_city or "").strip(),
    }


def run_search(form: Dict[str, Any]) -> None:
    errors = validate_travel_request(
        form["origin"], form["destination"], form["departure_date"], form["return_date"],
        form["adults"], form["children"], form["rooms"], form["children_ages"],
    )
    if errors:
        for message in errors:
            st.error(message)
        return

    # A new search must never be mixed with the previous one.
    st.session_state.pop("search", None)

    travel_request = {
        "origin": form["origin"],
        "destination": form["destination"],
        "origin_city": city_for_airport(form["origin"]),
        "destination_city": city_for_airport(form["destination"]),
        "departure_date": str(form["departure_date"]),
        "return_date": str(form["return_date"]),
        "adults": form["adults"],
        "children": form["children"],
        "children_ages": form["children_ages"],
        "rooms": form["rooms"],
        "stops": form["stops"],
        "city": form["hotel_city"],
        "currency": "USD",
    }
    try:
        with st.spinner("Searching flights and hotels... this can take up to a minute."):
            results = asyncio.run(process_travel_request(travel_request))
    except ValueError as exc:
        st.error(redact(exc))
        return
    except Exception as exc:
        st.error(f"The search failed: {redact(exc)}")
        return

    report = build_report(
        travel_request, results.get("flights"), results.get("hotels"),
        results.get("final_recommendations"), results.get("errors"),
    )
    # Build every download once, right now; button clicks later just serve these bytes.
    st.session_state["search"] = {
        "id": uuid.uuid4().hex,
        "report": report,
        "exports": build_all_exports(report),
        "signature": _form_signature({k: v for k, v in form.items() if k != "submit"}),
        "email_result": None,
    }


def render_summary(report: Dict[str, Any], form_changed: bool) -> None:
    req = report["request"]
    travellers = f"{req['adults']} adult(s)" + (f", {req['children']} child(ren)" if req["children"] else "")
    st.subheader(f"{req['origin']} → {req['destination']}")
    st.caption(
        f"{req['departure_date']} to {req['return_date']} ({req['nights']} nights) · {travellers} · "
        f"{req['rooms']} room(s) · {req['flight_stops_filter']} · prices in {req['currency']}"
    )
    st.caption(f"Generated {report['generated_at_display']}")
    if form_changed:
        st.info("The form has changed since these results were generated. Click **Search** to refresh them.")
    for message in report["errors"]:
        st.warning(message)


def render_exports(search: Dict[str, Any]) -> None:
    report, exports = search["report"], search["exports"]
    st.subheader("Download results")
    if not report["flights"] and not report["hotels"]:
        st.info("No flights or hotels were returned, so there is nothing to download.")
        return
    st.caption("Files are created from the results above - clicking a button does not search again.")
    order = ["pdf", "xlsx", "docx"]
    if report["flights"]:
        order.append("flights_csv")
    if report["hotels"]:
        order.append("hotels_csv")
    order.append("json")
    cols = st.columns(3)
    for i, key in enumerate(order):
        export = exports[key]
        with cols[i % 3]:
            if export.ok:
                _download_button(f"⬇ {export.label}", export.data, export.filename, export.mime, key=f"dl_{search['id']}_{key}")
            else:
                st.error(export.error or f"{export.label} could not be created.")


def render_flights(report: Dict[str, Any]) -> None:
    flights = report["flights"]
    if not flights:
        st.info("No flight options were returned for this search. Try different dates, or allow more stops under *More options*.")
        return
    currency = report["request"]["currency"]
    df = pd.DataFrame(
        {
            "Airline": [f["airline"] for f in flights],
            "Logo": [f["logo_url"] for f in flights],
            "Flight": [f["flight_number"] for f in flights],
            "Departure": [f["departure"] for f in flights],
            "Arrival": [f["arrival"] for f in flights],
            "Duration": [f["duration"] for f in flights],
            "Stops": [f["stops"] for f in flights],
            f"Price ({currency})": [f["price"] for f in flights],
            "Link": [f["booking_url"] for f in flights],
        }
    )
    fmt = f"{currency_symbol(currency)}%,.0f" if currency_symbol(currency) else "%,.0f"
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "Logo": st.column_config.ImageColumn("Logo", width="small"),
            f"Price ({currency})": st.column_config.NumberColumn(f"Price ({currency})", format=fmt),
            "Link": st.column_config.LinkColumn("Booking", display_text="Google Flights"),
        },
    )
    st.caption("Prices are search results, not confirmed fares. The link opens a Google Flights search for this whole query.")


def render_hotels(report: Dict[str, Any]) -> None:
    hotels = report["hotels"]
    if not hotels:
        st.info("No hotel options were returned for this search.")
        return
    currency = report["request"]["currency"]
    fmt = f"{currency_symbol(currency)}%,.0f" if currency_symbol(currency) else "%,.0f"
    df = pd.DataFrame(
        {
            "Photo": [h["image_url"] for h in hotels],
            "Hotel": [h["name"] for h in hotels],
            "Rating": [h["rating"] for h in hotels],
            "Reviews": [h["reviews"] for h in hotels],
            f"Per night ({currency})": [h["price_per_night"] for h in hotels],
            f"Total ({currency})": [h["total_price"] for h in hotels],
            "Amenities": [h["amenities"] or None for h in hotels],
            "Link": [h["booking_url"] for h in hotels],
        }
    )
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "Photo": st.column_config.ImageColumn("Photo", width="small"),
            "Rating": st.column_config.NumberColumn("Rating", format="%.1f"),
            f"Per night ({currency})": st.column_config.NumberColumn(f"Per night ({currency})", format=fmt),
            f"Total ({currency})": st.column_config.NumberColumn(f"Total ({currency})", format=fmt),
            "Link": st.column_config.LinkColumn("Website", display_text="Open"),
        },
    )
    st.caption("Prices are search results, not bookings. Hotel links go to the website/listing returned by the provider.")


def render_recommendations(report: Dict[str, Any]) -> None:
    recs = report["recommendations"]
    st.caption(recs.get("source_label") or "")
    for message in recs.get("warnings") or []:
        st.info(message)
    currency = report["request"]["currency"]
    shown = False
    for label, key, rows in (("Best flight", "best_flight", report["flights"]), ("Best hotel", "best_hotel", report["hotels"])):
        pick = recs.get(key)
        item = next((r for r in rows if pick and r["option"] == pick["option"]), None)
        if not (pick and item):
            continue
        shown = True
        with st.container(border=True):
            name = item.get("airline") if key == "best_flight" else item.get("name")
            st.markdown(f"**{label}: option {pick['option']}**")
            st.text(name or "n/a")
            price = item.get("price") if key == "best_flight" else item.get("price_per_night")
            suffix = "" if key == "best_flight" else " per night"
            st.text(f"Price: {fmt_money(price, currency)}{suffix}")
            if pick.get("reason"):
                st.markdown(_md_safe(pick["reason"]))
    if recs.get("summary"):
        shown = True
        st.markdown(_md_safe(recs["summary"]))
    if not shown:
        st.info("No recommendations could be produced from the returned data.")

    st.markdown("#### Estimated trip cost")
    scenarios = report["cost_estimate"]["scenarios"]
    if not scenarios:
        st.info("No estimate possible: no priced flight or hotel was returned.")
    for sc in scenarios:
        with st.container(border=True):
            st.markdown(f"**{_md_safe(sc['scenario'])}**")
            for item in sc["items"]:
                st.text(f"{item['label']}: {fmt_money(item['amount'], currency)}")
                st.caption(item["basis"])
            suffix = "" if sc["complete"] else "  (incomplete: flight or hotel price missing)"
            st.text(f"Total of items above: {fmt_money(sc['total'], currency)}{suffix}")
    with st.expander("What is NOT included in the estimate"):
        for text in report["cost_estimate"]["excluded"]:
            st.markdown(f"- {text}")
    with st.expander("Data sources and limitations"):
        for s in report["sources"]:
            st.markdown(f"- {_md_safe(s['type'])}: {_md_safe(s['name'])}")
        for note in report["notes"]:
            st.markdown(f"- {_md_safe(note)}")


def render_email(search: Dict[str, Any]) -> None:
    """Optional: email only happens when the user clicks the button below."""
    with st.expander("Email these results (optional)"):
        st.caption("Sending an email needs SENDGRID_KEY and SENDGRID_FROM_EMAIL to be configured. Downloads work without it.")
        to_email = st.text_input("Email address", key="email_to")
        if st.button("Send email now", key=f"send_{search['id']}"):
            if not is_valid_email(to_email):
                st.error("Please enter a valid email address.")
            else:
                with st.spinner("Sending..."):
                    payload = json.dumps({"to_email": to_email.strip(), "report": search["report"]}, default=str)
                    outcome = asyncio.run(EmailAgent().run([{"role": "user", "content": payload}]))
                search["email_result"] = {"success": outcome.get("success"), "error": outcome.get("error")}
        result = search.get("email_result")
        if result:
            if result["success"]:
                st.success("The email provider accepted the message for delivery.")
            else:
                st.error(result["error"] or "The email could not be sent.")


def main():
    st.set_page_config(page_title="AI Travel Analysis", page_icon="✈️", layout="wide")
    st.title("✈️ AI Travel Analysis")
    st.write("Search flights and hotels, compare them, and download the results as PDF, Excel, CSV, JSON or Word.")
    if not get_secret("SERPAPI_API_KEY"):
        st.warning("SERPAPI_API_KEY is not configured, so searches will fail until it is added (see README).")

    form = render_form()
    st.button("New search", on_click=reset_search, help="Clear the results and reset the form")
    if form["submit"]:
        run_search(form)

    search = st.session_state.get("search")
    if not search:
        return
    report = search["report"]
    current_signature = _form_signature({k: v for k, v in form.items() if k != "submit"})
    st.divider()
    render_summary(report, form_changed=current_signature != search["signature"])
    render_exports(search)
    tab_flights, tab_hotels, tab_recs = st.tabs(["Flights", "Hotels", "AI Recommendations"])
    with tab_flights:
        render_flights(report)
    with tab_hotels:
        render_hotels(report)
    with tab_recs:
        render_recommendations(report)
    render_email(search)


if __name__ == "__main__":
    main()
