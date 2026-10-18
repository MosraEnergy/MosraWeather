"""Provider adapters and mine weather screening. All internal units are metric/UTC."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UTC = timezone.utc
DB_PATH = Path(__file__).with_name("mine_weather.sqlite3")
DEFAULT_SITES = [
    {"name": "Headquarters · Abuja", "lat": 9.0579, "lon": 7.4951, "timezone": "Africa/Lagos", "verified": False},
    {"name": "Site Alpha · Kogi", "lat": 7.7969, "lon": 6.7333, "timezone": "Africa/Lagos", "verified": False},
    {"name": "Site Beta · Enugu", "lat": 6.4334, "lon": 7.4951, "timezone": "Africa/Lagos", "verified": False},
    {"name": "Site Gamma · Delta", "lat": 5.5325, "lon": 5.8987, "timezone": "Africa/Lagos", "verified": False},
]


@dataclass(frozen=True)
class Limits:
    rain_watch: float = 2.0
    rain_high: float = 7.5
    rain_24h_watch: float = 25.0
    rain_24h_high: float = 50.0
    visibility_watch: float = 2.0
    visibility_high: float = 0.5
    heat_watch: float = 35.0
    heat_high: float = 40.0

    def validate(self):
        for key, value in asdict(self).items():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a positive number.")
        for key in ("rain", "rain_24h", "heat"):
            if getattr(self, key + "_watch") >= getattr(self, key + "_high"):
                raise ValueError(f"{key}: high threshold must exceed watch threshold.")
        if self.visibility_high >= self.visibility_watch:
            raise ValueError("High-risk visibility must be below watch visibility.")
        return self


def validate_sites(sites):
    if not isinstance(sites, list) or not 1 <= len(sites) <= 30:
        raise ValueError("Provide between 1 and 30 sites.")
    clean, names = [], set()
    for site in sites:
        if not isinstance(site, dict):
            raise ValueError("Each site must be an object.")
        name = site.get("name", "")
        if not isinstance(name, str):
            raise ValueError("Site names must be text.")
        name = name.strip()
        if not name or len(name) > 80 or name.casefold() in names:
            raise ValueError("Site names must be unique and contain 1–80 characters.")
        lat, lon = float(site["lat"]), float(site["lon"])
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Latitude must be −90 to 90; longitude −180 to 180.")
        tz = str(site.get("timezone", "Africa/Lagos"))
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"Unknown timezone: {tz}") from None
        if not isinstance(site.get("verified", False), bool):
            raise ValueError("verified must be true or false.")
        names.add(name.casefold())
        clean.append(dict(name=name, lat=lat, lon=lon, timezone=tz, verified=site.get("verified", False)))
    return clean


def connect(path=DB_PATH):
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS bundles (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS observations (
        location TEXT, source TEXT, time TEXT, payload TEXT,
        PRIMARY KEY (location, source, time))""")
    return db


def load_setting(name, default, path=DB_PATH):
    with connect(path) as db:
        row = db.execute("SELECT payload FROM settings WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else default


def save_setting(name, value, path=DB_PATH):
    with connect(path) as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (name, json.dumps(value, allow_nan=False)))


def location_key(site):
    return f"{site['lat']:.6f},{site['lon']:.6f}"


def history(site, source, path=DB_PATH):
    with connect(path) as db:
        rows = db.execute("SELECT payload FROM observations WHERE location=? AND source=? ORDER BY time DESC LIMIT 500",
                          (location_key(site), source)).fetchall()
    return [json.loads(row[0]) for row in reversed(rows)]


class WeatherError(Exception):
    """Safe user-facing error; never contains request URLs or credentials."""


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def parse_time(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include timezone.")
    return dt.astimezone(UTC)


def stamp(dt):
    return dt.astimezone(UTC).isoformat()


def request_json(url, params):
    retry = Retry(total=2, backoff_factor=0.4, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], respect_retry_after_header=False)
    try:
        with requests.Session() as session:
            session.mount("https://", HTTPAdapter(max_retries=retry))
            response = session.get(url, params=params, timeout=(5, 15))
            if response.status_code in (401, 403):
                raise WeatherError("API key rejected. Check the key and your provider subscription.")
            if response.status_code == 429:
                raise WeatherError("Provider rate limit reached. Wait before refreshing.")
            if response.status_code != 200:
                raise WeatherError(f"Weather provider returned HTTP {response.status_code}.")
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                raise WeatherError("Provider returned an invalid response.")
            return payload
    except (requests.RequestException, ValueError):
        raise WeatherError("Weather service unavailable. Check connectivity and try again.") from None


def wmo_condition(code):
    code = number(code)
    if code is None:
        return "Unknown"
    if code == 0:
        return "Clear sky"
    if code in (1, 2):
        return "Partly cloudy"
    if code == 3:
        return "Overcast"
    if code in (45, 48):
        return "Fog"
    if code in (51, 53, 55, 56, 57):
        return "Drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "Rain / showers"
    if code in (71, 73, 75, 77, 85, 86):
        return "Snow"
    if code in (95, 96, 99):
        return "Thunderstorm"
    return "Unknown"


def openweather_point(item, current=False):
    main = item.get("main", {})
    weather = (item.get("weather") or [{}])[0]
    code = number(weather.get("id"))
    period = 1 if current else 3
    rain = number(item.get("rain", {}).get("1h" if current else "3h", 0))
    snow = number(item.get("snow", {}).get("1h" if current else "3h", 0))
    precip = rain + snow if rain is not None and snow is not None else None
    visibility = number(item.get("visibility"))
    pop = number(item.get("pop"))
    return dict(time=stamp(datetime.fromtimestamp(item["dt"], UTC)),
                temperature=number(main.get("temp")), feels_like=number(main.get("feels_like")),
                humidity=number(main.get("humidity")), pressure=number(main.get("pressure")),
                visibility=visibility / 1000 if visibility is not None else None,
                precipitation=precip, rain_rate=precip / period if precip is not None else None,
                rain_probability=pop * 100 if pop is not None else None, interval_hours=period,
                cloud=number(item.get("clouds", {}).get("all")), uv=None,
                storm=(200 <= code < 300) if code is not None else None,
                condition=str(weather.get("description", "Unknown")).title())


def fetch_openweather(site, key):
    params = dict(lat=site["lat"], lon=site["lon"], appid=key, units="metric")
    current = request_json("https://api.openweathermap.org/data/2.5/weather", params)
    forecast = request_json("https://api.openweathermap.org/data/2.5/forecast", params)
    point = openweather_point(current, True)
    rows = [openweather_point(item) for item in forecast["list"]]
    sun = current.get("sys", {})
    solar = {k: stamp(datetime.fromtimestamp(sun[k], UTC)) for k in ("sunrise", "sunset") if sun.get(k)}
    return dict(current=point, forecast=rows, solar=solar, source="OpenWeather", cadence=3,
                current_kind="Provider current estimate")


METEO_FIELDS = {"temperature": "temperature_2m", "feels_like": "apparent_temperature",
                "humidity": "relative_humidity_2m", "pressure": "pressure_msl",
                "visibility": "visibility",
                "precipitation": "precipitation", "rain_probability": "precipitation_probability",
                "cloud": "cloud_cover", "uv": "uv_index"}


def meteo_point(block, index=None):
    def val(key):
        values = block.get(key)
        return values[index] if index is not None and isinstance(values, list) and index < len(values) else values if index is None else None
    code = number(val("weather_code"))
    point = {name: number(val(field)) for name, field in METEO_FIELDS.items()}
    if point["visibility"] is not None:
        point["visibility"] /= 1000
    # API requests unix timestamps, so no naive local timestamps or DST ambiguity.
    point.update(time=stamp(datetime.fromtimestamp(val("time"), UTC)), interval_hours=1,
                 rain_rate=point["precipitation"], condition=wmo_condition(code),
                 storm=code in (95, 96, 99) if code is not None else None)
    return point


def fetch_meteo(site, key):
    params = dict(latitude=site["lat"], longitude=site["lon"],
                  hourly=",".join(METEO_FIELDS.values()) + ",weather_code",
                  daily="sunrise,sunset", timezone=site["timezone"], timeformat="unixtime",
                  forecast_days=7, apikey=key)
    raw = request_json("https://customer-api.open-meteo.com/v1/forecast", params)
    rows = [meteo_point(raw["hourly"], i) for i in range(len(raw["hourly"]["time"]))]
    now = datetime.now(UTC)
    current = max((r for r in rows if parse_time(r["time"]) <= now), key=lambda r: r["time"])
    solar = {}
    for name in ("sunrise", "sunset"):
        values = raw.get("daily", {}).get(name, [])
        if values and values[0] is not None:
            solar[name] = stamp(datetime.fromtimestamp(values[0], UTC))
    return dict(current=current, forecast=rows, solar=solar, source="Open-Meteo", cadence=1,
                current_kind="Hourly model estimate (not a site sensor)")


def get_bundle(site, provider, api_key="", force=False, path=DB_PATH, now=None):
    now = now or datetime.now(UTC)
    if provider not in ("OpenWeather", "Open-Meteo"):
        raise WeatherError("Select a supported weather provider.")
    if not api_key.strip():
        raise WeatherError("Enter an API key for the selected weather provider.")
    api_key = api_key.strip()
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    cache_key = f"v2:{provider}:{location_key(site)}:{site['timezone']}:{fingerprint}"
    with connect(path) as db:
        row = db.execute("SELECT payload FROM bundles WHERE key=?", (cache_key,)).fetchone()
    cached = json.loads(row[0]) if row else None
    if cached and not force and 0 <= (now - parse_time(cached["fetched_at"])).total_seconds() < 600:
        return dict(cached, stale=False, error=None)
    try:
        bundle = fetch_openweather(site, api_key) if provider == "OpenWeather" else fetch_meteo(site, api_key)
        if not bundle["forecast"] or bundle["current"]["temperature"] is None:
            raise WeatherError("Provider response is incomplete.")
    except (WeatherError, KeyError, TypeError, ValueError, OverflowError) as exc:
        # Do not expose underlying exceptions, which can include credential-bearing URLs.
        if cached:
            return dict(cached, stale=True, error="Refresh failed. Showing the last successful dataset.")
        message = str(exc) if isinstance(exc, WeatherError) else "Provider response is incomplete or malformed. Try again."
        raise WeatherError(message) from None
    bundle.update(fetched_at=stamp(now), stale=False, error=None)
    with connect(path) as db:
        db.execute("INSERT OR REPLACE INTO bundles VALUES (?, ?)", (cache_key, json.dumps(bundle)))
        db.execute("INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?)",
                   (location_key(site), provider, bundle["current"]["time"], json.dumps(bundle["current"])))
        db.execute("DELETE FROM observations WHERE time < ?", (stamp(now - timedelta(days=90)),))
    return bundle


@dataclass(frozen=True)
class Hazard:
    name: str
    level: int  # -1 unknown, 0 below thresholds, 1 watch, 2 high
    detail: str
    action: str


LABELS = {-1: "Data incomplete", 0: "Below thresholds", 1: "Watch", 2: "High risk"}
def assess(point, limits):
    hazards = []
    def threshold(name, key, watch, high, unit, action, reverse=False):
        value = number(point.get(key))
        if value is not None and key in ("visibility", "rain_rate") and value < 0:
            value = None
        if value is None:
            level = -1
        elif reverse:
            level = 2 if value <= high else 1 if value <= watch else 0
        else:
            level = 2 if value >= high else 1 if value >= watch else 0
        detail = "Not supplied by provider" if value is None else f"{value:.1f} {unit}"
        return Hazard(name, level, detail, action)
    hazards.append(threshold("Visibility", "visibility", limits.visibility_watch, limits.visibility_high, "km", "Review haul-road speed, spacing and dispatch; check visibility on site.", True))
    hazards.append(threshold("Rainfall", "rain_rate", limits.rain_watch, limits.rain_high, "mm/h interval average", "Inspect drains, sumps, pumps, road traction and pit access; check ponding and slope conditions."))
    hazards.append(threshold("Heat", "feels_like", limits.heat_watch, limits.heat_high, "°C feels like", "Apply the site heat plan: water, shade, work/rest and worker checks. Use on-site WBGT for exposure decisions."))
    storm = point.get("storm")
    hazards.append(Hazard("Thunderstorm", -1 if storm is None else 2 if storm else 0,
                          "Thunderstorm indicated" if storm else "Not supplied" if storm is None else "No thunderstorm code",
                          "Activate the site lightning plan; if thunder is heard, seek substantial shelter and wait at least 30 minutes after the last thunder."))
    return hazards


def overall(hazards):
    if not hazards:
        return -1
    levels = [h.level for h in hazards]
    if max(levels) > 0:
        return max(levels)
    return -1 if -1 in levels else 0


def quality(bundle, site, now=None):
    now = now or datetime.now(UTC)
    reasons = []
    if not site.get("verified"):
        reasons.append("Placeholder coordinates")
    if bundle.get("stale"):
        reasons.append("Refresh failed / cached data")
    age = (now - parse_time(bundle["current"]["time"])).total_seconds() / 60
    if age > 90:
        reasons.append(f"Current estimate is {age / 60:.1f} hours old")
    if age < -15:
        reasons.append("Current timestamp is in the future")
    return reasons


def upcoming(bundle, now=None):
    now = now or datetime.now(UTC)
    return sorted([r for r in bundle["forecast"] if parse_time(r["time"]) > now], key=lambda r: r["time"])


def rain_total(rows):
    values = [number(r.get("precipitation")) for r in rows]
    return sum(values) if values and all(v is not None and v >= 0 for v in values) else None


def drainage_level(total, limits):
    return -1 if total is None else 2 if total >= limits.rain_24h_high else 1 if total >= limits.rain_24h_watch else 0


