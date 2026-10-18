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
from utils.display import (
    FLIGHT_SORTS, HOTEL_SORTS, day_offset, fmt_date, fmt_time, flight_highlights,
    hotel_highlights, md_link, stops_text,
)
from utils.export_reports import build_all_exports
from utils.report_data import (
    FLIGHT_COLUMNS,
    STOPS_LABELS,
    build_report,
    currency_symbol,
    fmt_money,
)
from utils.validation import (
    airport_choices,
    city_for_airport,
    code_from_label,
    is_valid_email,
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
    labels, _ = airport_choices()
    origin_label = c1.selectbox(
        "Origin airport", options=labels, index=None, key="origin",
        placeholder="Type a code or airport name, e.g. DAC", help="Start typing to search all airports.",
    )
    destination_label = c2.selectbox(
        "Destination airport", options=labels, index=None, key="destination",
        placeholder="Type a code or airport name, e.g. AMS", help="Start typing to search all airports.",
    )
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
        "origin": code_from_label(origin_label),
        "destination": code_from_label(destination_label),
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


def _badges(*pairs) -> str:
    return " ".join(f":{color}-badge[{text}]" for color, text in pairs if text)


def _sorted(rows, sorts, label, key):
    choice = st.selectbox(label, options=list(sorts.keys()), key=key)
    return sorted(rows, key=sorts[choice])


def _show_limit(total: int, key: str, step: int = 10) -> int:
    if total <= step:
        return total
    return total if st.toggle(f"Show all {total} options (showing {step})", key=key) else step


def render_flights(report: Dict[str, Any]) -> None:
    flights = report["flights"]
    if not flights:
        st.info("No flight options were returned for this search. Try different dates, or allow more stops under *More options*.")
        return
    currency = report["request"]["currency"]
    hi = flight_highlights(flights)
    m1, m2, m3 = st.columns(3)
    m1.metric("Flights found", len(flights))
    cheapest, fastest = hi["cheapest"], hi["fastest"]
    m2.metric("Cheapest", fmt_money(cheapest["price"], currency) if cheapest else "n/a", cheapest["airline"] if cheapest else None, delta_color="off")
    m3.metric("Fastest", fastest["duration"] if fastest else "n/a", fastest["airline"] if fastest else None, delta_color="off")

    rows = _sorted(flights, FLIGHT_SORTS, "Sort flights by", "sort_flights")
    for f in rows[: _show_limit(len(rows), "show_all_flights")]:
        with st.container(border=True):
            c_logo, c_main, c_route, c_price = st.columns([1, 3, 5, 3], vertical_alignment="center")
            with c_logo:
                if f.get("logo_url"):
                    st.image(f["logo_url"], width=48)
            with c_main:
                st.markdown(f"**{_md_safe(f.get('airline') or 'Airline n/a')}**")
                st.caption(_md_safe(f.get("flight_number") or "Flight number n/a"))
                st.markdown(_badges(
                    ("green", "Cheapest" if f is cheapest else ""),
                    ("blue", "Fastest" if f is fastest else ""),
                    ("orange", "Google top pick" if f.get("top_pick") else ""),
                ))
            with c_route:
                offset = day_offset(f.get("departure"), f.get("arrival"))
                st.markdown(
                    f"### {fmt_time(f.get('departure'))} → {fmt_time(f.get('arrival'))}"
                    + (f" <sup>{offset}</sup>" if offset else ""),
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"{_md_safe(f.get('departure_airport') or '')} → {_md_safe(f.get('arrival_airport') or '')}"
                    f" · {_md_safe(f.get('duration') or 'duration n/a')} · {stops_text(f.get('stops'))}"
                    + (f" · {fmt_date(f.get('departure'))}" if fmt_date(f.get("departure")) else "")
                )
            with c_price:
                st.markdown(f"### {_md_safe(fmt_money(f.get('price'), currency))}")
                if f.get("booking_url"):
                    st.markdown(md_link(f["booking_url"], "View on Google Flights ↗"))
    st.caption("Prices are search results, not confirmed fares. The link opens a Google Flights search for this whole query.")

    with st.expander("Table view (sortable, for comparing)"):
        df = pd.DataFrame(
            {
                "Airline": [f["airline"] for f in flights],
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
                f"Price ({currency})": st.column_config.NumberColumn(f"Price ({currency})", format=fmt),
                "Link": st.column_config.LinkColumn("Booking", display_text="Google Flights"),
            },
        )


def render_hotels(report: Dict[str, Any]) -> None:
    hotels = report["hotels"]
    if not hotels:
        st.info("No hotel options were returned for this search.")
        return
    currency = report["request"]["currency"]
    hi = hotel_highlights(hotels)
    top, low = hi["top_rated"], hi["lowest_price"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Hotels found", len(hotels))
    m2.metric("Top rated", f"{top['rating']:.1f} ★" if top else "n/a", top["name"] if top else None, delta_color="off")
    m3.metric("Lowest per night", fmt_money(low["price_per_night"], currency) if low else "n/a", low["name"] if low else None, delta_color="off")

    rows = _sorted(hotels, HOTEL_SORTS, "Sort hotels by", "sort_hotels")
    for h in rows[: _show_limit(len(rows), "show_all_hotels")]:
        with st.container(border=True):
            c_img, c_info, c_price = st.columns([2, 6, 3], vertical_alignment="center")
            with c_img:
                if h.get("image_url"):
                    st.image(h["image_url"], width=140)
                else:
                    st.caption("No photo")
            with c_info:
                st.markdown(f"**{_md_safe(h.get('name'))}**")
                bits = []
                if h.get("hotel_class"):
                    bits.append(_md_safe(h["hotel_class"]))
                if h.get("rating") is not None:
                    reviews = f" ({h['reviews']:,} reviews)" if isinstance(h.get("reviews"), int) else ""
                    bits.append(f"{h['rating']:.1f} ★{reviews}")
                if bits:
                    st.caption(" · ".join(bits))
                st.markdown(_badges(
                    ("green", "Top rated" if h is top else ""),
                    ("blue", "Lowest price" if h is low else ""),
                ))
                amenities = h.get("amenities_list") or []
                if amenities:
                    shown = ", ".join(_md_safe(a) for a in amenities[:5])
                    more = f" +{len(amenities) - 5} more" if len(amenities) > 5 else ""
                    st.caption(shown + more)
            with c_price:
                st.markdown(f"### {_md_safe(fmt_money(h.get('price_per_night'), currency))}")
                st.caption("per night" if h.get("price_per_night") is not None else "price not available")
                if h.get("total_price") is not None:
                    st.caption(f"Total stay: {_md_safe(fmt_money(h['total_price'], currency))}")
                if h.get("booking_url"):
                    st.markdown(md_link(h["booking_url"], "Visit website ↗"))
    st.caption("Prices are search results, not bookings. Hotel links go to the website/listing returned by the provider.")

    with st.expander("Table view (sortable, for comparing)"):
        fmt = f"{currency_symbol(currency)}%,.0f" if currency_symbol(currency) else "%,.0f"
        df = pd.DataFrame(
            {
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
                "Rating": st.column_config.NumberColumn("Rating", format="%.1f"),
                f"Per night ({currency})": st.column_config.NumberColumn(f"Per night ({currency})", format=fmt),
                f"Total ({currency})": st.column_config.NumberColumn(f"Total ({currency})", format=fmt),
                "Link": st.column_config.LinkColumn("Website", display_text="Open"),
            },
        )


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
