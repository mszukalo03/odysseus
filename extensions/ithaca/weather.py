"""
extensions/ithaca/weather.py

Live weather data for Ithaca's one persistent built-in tile. Split out of
backend.py (now just route wiring) the same way tile query/layout logic
lives in tiles.py — each concern gets its own module instead of one huge
backend.py.

`get_weather()` is the single entry point: both the HTTP route
(backend.py) and the agent tool (src/tools/ithaca.py) call it directly
instead of reaching into cache internals, so there's exactly one place
that knows how weather is fetched/cached.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict

import httpx
from fastapi import HTTPException

from core.ttl_cache import TTLCache

WEATHER_CACHE_TTL = 10 * 60   # OpenWeatherMap free tier: no need to re-poll faster
FORECAST_SLOTS = 9            # 9 × 3h ≈ next 27 hours

_cache = TTLCache()


def _setting_or_env(setting_key: str, env_var: str, default: str = "") -> str:
    """Resolve a config value: a value saved in Settings > Integrations wins,
    falling back to the env var, then `default`. Mirrors
    services/search/providers.py's `_get_provider_key`/`_get_search_instance`
    pattern used for the other UI-configurable API keys."""
    try:
        from src.settings import get_setting
        val = (get_setting(setting_key) or "").strip()
        if val:
            return val
    except Exception:
        pass
    return (os.getenv(env_var) or default).strip()


def weather_settings() -> Dict[str, str]:
    return {
        "api_key": _setting_or_env("openweather_api_key", "OPENWEATHER_API_KEY"),
        "lat": _setting_or_env("openweather_lat", "OPENWEATHER_LAT"),
        "lon": _setting_or_env("openweather_lon", "OPENWEATHER_LON"),
        "units": _setting_or_env("openweather_units", "OPENWEATHER_UNITS", "metric"),
    }


async def _fetch_weather() -> Dict[str, Any]:
    cfg = weather_settings()
    api_key = cfg["api_key"]
    lat = cfg["lat"]
    lon = cfg["lon"]
    units = cfg["units"]
    if not api_key:
        raise HTTPException(503, "OPENWEATHER_API_KEY is not configured")
    if not lat or not lon:
        raise HTTPException(503, "OPENWEATHER_LAT / OPENWEATHER_LON are not configured")

    params = {"lat": lat, "lon": lon, "units": units, "appid": api_key}
    # Overridable for proxies/tests; production default is the real API.
    base = (os.getenv("OPENWEATHER_API_BASE") or "https://api.openweathermap.org").rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        current_resp, forecast_resp = await asyncio.gather(
            client.get(f"{base}/data/2.5/weather", params=params),
            client.get(f"{base}/data/2.5/forecast", params=params),
        )
    if current_resp.status_code == 401 or forecast_resp.status_code == 401:
        raise HTTPException(502, "OpenWeatherMap rejected the API key")
    if current_resp.status_code != 200 or forecast_resp.status_code != 200:
        raise HTTPException(
            502,
            f"OpenWeatherMap error (weather={current_resp.status_code}, "
            f"forecast={forecast_resp.status_code})",
        )
    cur = current_resp.json()
    fc = forecast_resp.json()

    def _cond(block: dict) -> Dict[str, Any]:
        w = (block.get("weather") or [{}])[0]
        return {"description": w.get("description", ""), "icon": w.get("icon", "")}

    tz_offset = int(fc.get("city", {}).get("timezone", cur.get("timezone", 0)) or 0)
    hourly = []
    for entry in (fc.get("list") or [])[:FORECAST_SLOTS]:
        hourly.append({
            "dt": entry.get("dt"),
            "local_hour": ((int(entry.get("dt", 0)) + tz_offset) // 3600) % 24,
            "temp": entry.get("main", {}).get("temp"),
            "feels_like": entry.get("main", {}).get("feels_like"),
            "humidity": entry.get("main", {}).get("humidity"),
            "pop": round(float(entry.get("pop") or 0) * 100),
            **_cond(entry),
        })
    return {
        "location": fc.get("city", {}).get("name") or cur.get("name") or "",
        "units": units,
        "current": {
            "temp": cur.get("main", {}).get("temp"),
            "feels_like": cur.get("main", {}).get("feels_like"),
            "humidity": cur.get("main", {}).get("humidity"),
            "wind_speed": cur.get("wind", {}).get("speed"),
            **_cond(cur),
        },
        "hourly": hourly,
        "fetched_at": int(time.time()),
    }


async def get_weather(refresh: bool = False) -> Dict[str, Any]:
    """Current conditions + hourly forecast, single-flight TTL-cached (see
    core/ttl_cache.py) so concurrent tile/tool calls share one upstream
    request."""
    cfg = weather_settings()
    key = "|".join((cfg["lat"], cfg["lon"], cfg["units"]))
    if refresh:
        _cache.invalidate(key)
    return await _cache.get(key, WEATHER_CACHE_TTL, _fetch_weather)
