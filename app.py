"""Mosra Energy mine weather workspace. Run: streamlit run app.py."""
from __future__ import annotations

import html
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from weather import (
    DEFAULT_SITES, LABELS, Limits, UTC, WeatherError, assess,
    drainage_level, get_bundle, load_setting, number, overall, parse_time,
    quality, rain_total, save_setting, upcoming, validate_sites,
)

st.set_page_config(page_title="Mosra · Mine Weather", page_icon="⛏️", layout="wide")
COLORS = {-1: "#8295ac", 0: "#2dd4bf", 1: "#fbbf24", 2: "#fb7185"}
st.markdown("""
<style>
.stApp {background: #0b1220; color: #e2e8f0;}
[data-testid="stSidebar"] {background: #101c2e; border-right: 1px solid #24324a;}
.block-container {padding-top: 4.5rem; max-width: 1600px;}
h1, h2, h3 {letter-spacing: -.035em;}
[data-testid="stMetric"] {background: #142238; border: 1px solid #293b54; border-radius: 14px; padding: 18px;}
[data-testid="stMetricLabel"] {color: #9db0c8;}
[data-testid="stMetricValue"] {font-size: 1.8rem;}
.eyebrow {font-size: .76rem; color: #2dd4bf; letter-spacing: .16em; font-weight: 700; text-transform: uppercase;}
.hero {border: 1px solid #304660; border-radius: 18px; padding: 24px; background: linear-gradient(115deg, #153047, #142137); margin-bottom: 22px;}
.hero h2 {margin: 8px 0; font-size: 2rem; color: #f1f5f9;}
.hero p {margin: 0; color: #adbed2;}
.status {display: inline-block; padding: 5px 12px; border: 1px solid currentColor; border-radius: 100px; font-size: .82rem; font-weight: 600;}
.hazard {background: #142238; border: 1px solid #293b54; border-radius: 12px; padding: 16px; margin: 10px 0;}
.hazard p {font-size: .9rem; color: #b9c8db; margin: 9px 0 0;}
.stTabs [data-baseweb="tab-list"] {gap: 18px;}
@media(max-width: 700px) {.block-container {padding: 4rem 1rem 1rem;} .hero {padding: 16px;} .hero h2 {font-size: 1.5rem;}}
</style>
""", unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def fmt(value, unit="", digits=1):
    value = number(value)
    return "Unavailable" if value is None else f"{value:,.{digits}f}{unit}"


def local_time(value, site, pattern="%d %b, %H:%M"):
    return parse_time(value).astimezone(ZoneInfo(site["timezone"])).strftime(pattern)


def secret(name):
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, ""))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return ""


def badge(level):
    return f'<span class="status" style="color:{COLORS[level]}">{LABELS[level]}</span>'


def frame(rows, site, limits):
    df = pd.DataFrame(rows)
    if not df.empty:
        df["local_time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(site["timezone"])
        df["level"] = [overall(assess(r, limits)) for r in rows]
        df["screening"] = df["level"].map(LABELS)
    return df


def threshold_settings(limits):
    st.caption("Starting values are illustrative screening thresholds. Replace them with your approved site limits. These settings apply to all sites.")
    with st.form("threshold_form"):
        a, b = st.columns(2)
        values = {}
        definitions = [("rain", "Rain intensity (mm/h)", a), ("rain_24h", "Forecast 24h rain (mm)", a),
                       ("visibility", "Visibility (km; lower is worse)", b), ("heat", "Feels-like temperature (°C)", b)]
        for key, label, col in definitions:
            col.markdown(f"**{label}**")
            for suffix, title in [("_watch", "Watch"), ("_high", "High risk")]:
                values[key + suffix] = col.number_input(title, min_value=0.1, max_value=500.0,
                    value=float(getattr(limits, key + suffix)), step=0.1, key=key + suffix)
        if st.form_submit_button("Save thresholds", type="primary"):
            try:
                new_limits = Limits(**values).validate()
                save_setting("limits", asdict(new_limits))
                st.rerun()
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))


def site_settings(sites):
    st.caption("Replace the example locations with exact mine coordinates. Verified means you have checked the coordinates; it does not certify weather accuracy.")
    edited = st.data_editor(pd.DataFrame(sites), hide_index=True, num_rows="dynamic", width="stretch",
        column_config={"name": st.column_config.TextColumn("Site name", required=True),
                       "lat": st.column_config.NumberColumn("Latitude", min_value=-90.0, max_value=90.0, format="%.6f", required=True),
                       "lon": st.column_config.NumberColumn("Longitude", min_value=-180.0, max_value=180.0, format="%.6f", required=True),
                       "timezone": st.column_config.TextColumn("IANA timezone", required=True),
                       "verified": st.column_config.CheckboxColumn("Coordinates verified", default=False)}, key="site_editor")
    if st.button("Save site directory", type="primary"):
        try:
            save_setting("sites", validate_sites(edited.to_dict("records")))
            st.rerun()
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            st.error(f"Could not save sites: {exc}")
    st.download_button("Export site directory", json.dumps(sites, indent=2), "mine_sites.json", "application/json")
    upload = st.file_uploader("Import a site directory", type="json")
    if upload is not None and st.button("Validate and import"):
        try:
            if upload.size > 100_000:
                raise ValueError("File is too large (maximum 100 KB).")
            save_setting("sites", validate_sites(json.loads(upload.getvalue())))
            st.rerun()
        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            st.error(f"Import rejected: {exc}")


try:
    sites = validate_sites(load_setting("sites", DEFAULT_SITES))
    saved_limits = load_setting("limits", asdict(Limits()))
    limits = Limits(**{key: value for key, value in saved_limits.items() if key in asdict(Limits())}).validate()
except (sqlite3.Error, ValueError, TypeError, KeyError):
    st.error("Local settings could not be loaded. Check the database and file permissions.")
    st.stop()

with st.sidebar:
    st.markdown('<div class="eyebrow">MOSRA ENERGY</div>', unsafe_allow_html=True)
    st.title("Mine Weather")
    st.caption("Operations intelligence")
    selected = st.selectbox("Active site", [s["name"] for s in sites])
    site = next(s for s in sites if s["name"] == selected)
    st.caption(f"{site['lat']:.4f}° / {site['lon']:.4f}°\n\n{site['timezone']}")
    st.divider()
    saved_key, meteo_key = secret("OPENWEATHER_API_KEY"), secret("OPEN_METEO_API_KEY")
    provider = st.selectbox("Weather source", ["OpenWeather", "Open-Meteo"], index=1 if meteo_key and not saved_key else 0)
    api_key = st.text_input(f"{provider} API key", value=saved_key if provider == "OpenWeather" else meteo_key, type="password")
    st.caption("Keys stay out of the local database.")
    if provider == "Open-Meteo":
        st.caption("Uses the commercial customer endpoint. Requires an Open-Meteo subscription.")
    refresh_minutes = st.selectbox("Automatic refresh", [0, 5, 10, 15], index=2, format_func=lambda n: "Off" if n == 0 else f"Every {n} minutes")


def render_overview(bundle, site, limits, rows):
    point, hazards = bundle["current"], assess(bundle["current"], limits)
    reasons = quality(bundle, site)
    level = overall(hazards)
    status = "Review data quality" if reasons else LABELS[level]
    tone = COLORS[-1] if reasons else COLORS[level]
    st.markdown(f'<div class="hero"><div class="eyebrow">SITE WEATHER BRIEF</div><h2>{esc(site["name"])}</h2><p>{esc(point["condition"])} · {esc(local_time(point["time"], site))} · {esc(site["timezone"])}</p><div style="margin-top:16px;color:{tone}" class="status">{esc(status)}</div></div>', unsafe_allow_html=True)
    if reasons:
        st.warning(" · ".join(reasons) + ". Confirm live site conditions before using this screen for operational decisions.")
    st.caption(f"{bundle['source']} · {bundle['current_kind']} · Retrieved {local_time(bundle['fetched_at'], site)} · All times in {site['timezone']}")
    cols = st.columns(3)
    cols[0].metric("Air temperature", fmt(point.get("temperature"), " °C"))
    cols[0].caption(f"Feels like {fmt(point.get('feels_like'), ' °C')}")
    cols[1].metric("Visibility", fmt(point.get("visibility"), " km"))
    cols[1].caption("Check haul-road conditions")
    cols[2].metric("Rain intensity", fmt(point.get("rain_rate"), " mm/h"))
    cols[2].caption("Average over provider interval")
    st.write("")
    left, right = st.columns([1.5, 1])
    with left:
        st.subheader("Operational watchboard")
        for hazard in sorted(hazards, key=lambda h: (h.level != 2, h.level != 1, h.level != -1)):
            action = hazard.action if hazard.level != 0 else "No configured threshold exceeded; confirm on-site conditions."
            st.markdown(f'<div class="hazard"><strong>{esc(hazard.name)}</strong> &nbsp; {badge(hazard.level)}<p>{esc(hazard.detail)}</p><p>{esc(action)}</p></div>', unsafe_allow_html=True)
    with right:
        st.subheader("Next 24 hours")
        start = parse_time(rows[0]["time"]) - timedelta(hours=bundle["cadence"]) if rows else None
        next24 = [r for r in rows if start and parse_time(r["time"]) <= start + timedelta(hours=24)]
        total, coverage = rain_total(next24), len(next24) * bundle["cadence"]
        st.metric("Forecast precipitation", fmt(total, " mm"))
        st.markdown(badge(drainage_level(total, limits) if coverage == 24 else -1), unsafe_allow_html=True)
        st.caption(f"{coverage}h of forecast intervals, beginning {local_time(start.isoformat(), site) if start else '—'}. Includes rain and snow water equivalent. Antecedent rainfall is not measured here.")
        high = [r for r in next24 if overall(assess(r, limits)) == 2]
        if high:
            st.warning(f"First high-risk forecast: {local_time(high[0]['time'], site)}. Review shift controls.")
        elif next24:
            st.info("Review the forecast for watch periods and missing data.")
        st.subheader("Supporting conditions")
        for label, key, unit in [("Humidity", "humidity", "%"), ("Pressure", "pressure", " hPa"), ("Cloud cover", "cloud", "%"), ("UV index", "uv", "")]:
            st.write(f"{label}: **{fmt(point.get(key), unit)}**")
        for label, value in bundle.get("solar", {}).items():
            st.write(f"{label.title()}: **{local_time(value, site, '%H:%M')}**")


def render_forecast(bundle, site, limits, df):
    if df.empty:
        st.warning("No future forecast intervals available. Refresh the data.")
        return
    st.subheader("Forecast explorer")
    st.caption(f"{bundle['cadence']}-hour provider resolution. Forecasts are not interpolated to invent hourly values.")
    horizon = st.select_slider("Forecast horizon", options=[24, 48, 72, 120, 168], value=72, format_func=lambda n: f"{n} hours")
    shown = df[df["local_time"] <= datetime.now(UTC) + timedelta(hours=int(horizon))]
    chart_type = st.radio("View", ["Temperature & heat", "Rain & drainage", "Visibility"], horizontal=True)
    fig = make_subplots(specs=[[{"secondary_y": chart_type == "Rain & drainage"}]])
    x = shown["local_time"]
    if chart_type == "Temperature & heat":
        fig.add_trace(go.Scatter(x=x, y=shown["temperature"], name="Air temperature", line=dict(color="#2dd4bf", width=3)))
        fig.add_trace(go.Scatter(x=x, y=shown["feels_like"], name="Feels like", line=dict(color="#fbbf24", dash="dot")))
        fig.add_hline(y=limits.heat_watch, line_dash="dash", line_color="#fbbf24", annotation_text="Heat watch")
        fig.update_yaxes(title_text="°C")
    elif chart_type == "Rain & drainage":
        fig.add_trace(go.Bar(x=x, y=shown["precipitation"], name=f"Precipitation / {bundle['cadence']}h", marker_color="#38bdf8"), secondary_y=False)
        fig.add_trace(go.Scatter(x=x, y=shown["rain_probability"], name="Precipitation probability", line=dict(color="#a78bfa")), secondary_y=True)
        fig.update_yaxes(title_text="mm per interval", secondary_y=False)
        fig.update_yaxes(title_text="Probability (%)", range=[0, 100], secondary_y=True)
        st.caption("Rain probability and rainfall amount describe different risks. Interval averages can conceal short downpours; inspect roads, drains and sumps on site.")
    else:
        fig.add_trace(go.Scatter(x=x, y=shown["visibility"], name="Visibility", line=dict(color="#38bdf8", width=3)))
        fig.add_hline(y=limits.visibility_watch, line_dash="dash", line_color="#fbbf24", annotation_text="Watch visibility")
        fig.update_yaxes(title_text="km")
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#101c2e", height=390,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.15), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")
    st.subheader("Daily outlook")
    daily = df.groupby(df["local_time"].dt.date).agg(
        low=("temperature", "min"), high=("temperature", "max"), feels=("feels_like", "max"),
        rain=("precipitation", lambda s: s.sum(min_count=len(s))), probability=("rain_probability", "max"),
        visibility=("visibility", "min"), intervals=("time", "count"))
    daily["coverage"] = daily["intervals"] * bundle["cadence"]
    daily = daily.drop(columns="intervals").round(1)
    daily.columns = ["Low °C", "High °C", "Max feels °C", "Rain mm", "Rain chance %", "Min visibility km", "Coverage h"]
    st.dataframe(daily, width="stretch")
    st.caption("Daily summaries cover available future intervals. First and last days may be partial; precipitation is grouped by interval-end date.")


def render_network(site, provider, api_key, limits, active_bundle):
    st.subheader("Site network")
    st.map(pd.DataFrame(sites)[["lat", "lon"]], zoom=5, width="stretch")
    st.caption("Map tiles require internet. Coordinates remain listed below when the map is unavailable.")
    chosen = st.multiselect("Sites to compare", [s["name"] for s in sites], default=[site["name"]], max_selections=8)
    comparisons = []
    for candidate in sites:
        if candidate["name"] not in chosen:
            continue
        try:
            with st.spinner(f"Loading {candidate['name']}…"):
                data = active_bundle if candidate["name"] == site["name"] else get_bundle(candidate, provider, api_key)
            p, problems = data["current"], quality(data, candidate)
            comparisons.append({"Site": candidate["name"], "Coordinates": f"{candidate['lat']:.4f}, {candidate['lon']:.4f}",
                "Temperature °C": p["temperature"],
                "Visibility km": p["visibility"], "Weather screen": LABELS[overall(assess(p, limits))],
                "Data quality": "; ".join(problems) or "Current provider estimate", "Estimate time": local_time(p["time"], candidate) + " " + candidate["timezone"]})
        except (WeatherError, sqlite3.Error) as exc:
            comparisons.append({"Site": candidate["name"], "Data quality": str(exc)})
    if comparisons:
        st.dataframe(pd.DataFrame(comparisons), hide_index=True, width="stretch")


def render_management():
    site_tab, threshold_tab, guide_tab = st.tabs(["Site directory", "Thresholds", "Data & guidance"])
    with site_tab:
        site_settings(sites)
    with threshold_tab:
        threshold_settings(limits)
    with guide_tab:
        st.markdown("""
        **Coverage.** OpenWeather: current estimates and five days at three-hour intervals. Open-Meteo: seven calendar days at hourly resolution via its commercial customer API. Missing fields remain unavailable.

        **Rules.** High risk takes precedence over watch. If a required field is missing and supplied fields do not exceed a limit, the screen is incomplete. Thunderstorms use condition codes; this app has no lightning detector or official warning feed.

        **Drainage.** Intensity is interval amount divided by duration, not peak rainfall. Accumulation screens cover available future forecast intervals. They cannot determine flooding, sump capacity, slope stability, or traction.

        **Heat.** Feels-like temperature is a screening proxy, not WBGT or an occupational exposure limit. Apply your site's exposure measurements and work/rest procedures.

        **Storage.** Sites, thresholds, caches and up to 90 days of provider estimates stay in local SQLite. Keys are not stored there. Multi-user deployments share settings and need access control before use.

        [NiMet official weather information](https://nimet.gov.ng/) · [OpenWeather docs](https://openweathermap.org/forecast5) · [Open-Meteo docs](https://open-meteo.com/en/docs) · [Lightning safety guidance](https://www.weather.gov/safety/lightning-tips)
        """)


@st.fragment(run_every=timedelta(minutes=refresh_minutes) if refresh_minutes else None)
def dashboard():
    top, action = st.columns([5, 1])
    top.markdown('<div class="eyebrow">OPERATIONS / ENVIRONMENT</div>', unsafe_allow_html=True)
    top.title("Mine site weather dashboard")
    force = action.button("↻ Refresh now", width="stretch")
    overview, forecast, network, management = st.tabs(["Overview", "Forecast", "Site network", "Manage sites & weather thresholds"])
    with management:
        render_management()
    try:
        with st.spinner("Updating site weather…"):
            bundle = get_bundle(site, provider, api_key, force=force)
    except (WeatherError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, WeatherError) else "Weather cache could not be accessed. Check database permissions."
        with overview:
            st.error(message)
            st.info("Configure your weather provider API key in the sidebar to load weather data.")
        with forecast:
            st.info("Forecast data will appear after connecting a weather provider.")
        with network:
            st.info("Site weather comparisons will appear after connecting a weather provider.")
        return
    if bundle.get("error"):
        st.error(bundle["error"])
    rows = upcoming(bundle)
    df = frame(rows, site, limits)
    with overview:
        render_overview(bundle, site, limits, rows)
    with forecast:
        render_forecast(bundle, site, limits, df)
    with network:
        render_network(site, provider, api_key, limits, bundle)
    st.divider()
    st.caption("On-site measurements, approved procedures and the responsible supervisor govern operations.")
    if bundle["source"] == "Open-Meteo":
        st.markdown("Weather data: [Open-Meteo](https://open-meteo.com/) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)")
    elif bundle["source"] == "OpenWeather":
        st.markdown("Weather data: [OpenWeather](https://openweathermap.org/)")


dashboard()
