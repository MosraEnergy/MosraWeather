# Mosra Energy · Mine Weather

A Streamlit weather workspace with a current hazard watchboard, interactive forecasts, site comparisons, and a separate tab for site and threshold management.

## Run locally

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Choose **OpenWeather** or **Open-Meteo** and enter a provider key to load weather. Site and threshold management remains available before connecting a provider.

## Configure real sites and weather

1. Open the **Manage sites & weather thresholds** tab. Replace the placeholder sites with exact mine coordinates, enter an IANA timezone such as `Africa/Lagos`, and mark coordinates verified after checking them. You can also import or export the directory as JSON.
2. Select **OpenWeather** in the sidebar and enter your API key. This retains the original app's provider: current conditions and a five-day forecast at three-hour resolution. Use an account licensed for your intended use.
3. Alternatively, select **Open-Meteo** and enter a commercial subscription key. This uses the customer API and provides hourly forecasts for seven calendar days. The free Open-Meteo endpoint is not used because this app is for company operations.
4. Replace the illustrative screening thresholds with limits approved for your sites and equipment. Thresholds currently apply across the site directory.

Keys can be supplied through the environment as `OPENWEATHER_API_KEY` / `OPEN_METEO_API_KEY`, or through an ignored `.streamlit/secrets.toml` file:

```toml
OPENWEATHER_API_KEY = "your-key"
# OPEN_METEO_API_KEY = "your-commercial-key"
```

Keys are not written to the database. Provider URLs and raw network errors are never displayed. Requests use HTTPS, timeouts and bounded retries.

## Operational behavior

- Visibility, interval-average rain intensity, feels-like temperature and thunderstorm codes drive hazard screening. High risk takes precedence over watch; required missing data stays incomplete.
- The drainage screen compares forecast precipitation accumulated over 24 hours to configured limits. It has no measured antecedent rainfall, water-level, pump or slope data.
- All timestamps are timezone aware. Charts and daily summaries use the selected site's timezone. Partial days show their forecast coverage.
- Successful weather requests are cached for ten minutes. **Refresh now** bypasses the cache. Failed refreshes can display the last dataset with a prominent stale-data flag and original timestamps. Weather failures do not substitute generated data.
- Saved threshold settings from earlier versions remain compatible; obsolete fields are ignored and omitted when settings are saved again. Provider caches use a revised format so earlier datasets are refreshed.

## Local storage and deployment

`mine_weather.sqlite3` stores site settings, screening thresholds, provider caches, and distinct retrieved current estimates for up to 90 days. These estimates are **not site sensor readings** and are not a historical weather archive. Settings and data are shared by users of the same app instance.

The original `weather_cache.db` is left untouched. Back up the new SQLite database if you need to preserve settings and history. Use a persistent writable volume in a hosted deployment. There is no login system; deploy on a protected internal network or behind your organization's authentication gateway before sharing it. Site editing is intended for trusted operators.

This app does not include instrumented lightning detection, official warning ingestion, measured dust concentration, WBGT, geotechnical monitoring, email/SMS delivery, or background monitoring when no browser session is open. On-screen alerts require someone to monitor the dashboard. Connect those services separately when available; model forecasts cannot substitute for them.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover risk boundaries, unknown fields, interval units, timezone handling, rainfall accumulation, cache fallback, secret redaction, and settings/history persistence. Streamlit application tests use mocked provider responses to check live source selection, management without a key, chart controls, removed features, and compatibility with saved settings.

## Sources

- [OpenWeather five-day forecast API](https://openweathermap.org/forecast5)
- [Open-Meteo API documentation](https://open-meteo.com/en/docs) and [commercial terms](https://open-meteo.com/en/terms)
- [NiMet official weather information](https://nimet.gov.ng/)
- [National Weather Service lightning guidance](https://www.weather.gov/safety/lightning-tips)

Defaults are illustrative engineering screens, not regulatory or manufacturer operating limits. Site procedures and responsible supervisors govern operational decisions.
