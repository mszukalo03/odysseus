"""Ithaca-domain tool implementations.

Read-only agent access to the Ithaca hub's live tiles: local weather
(queried directly from OpenWeatherMap) and the software-updates digest
(parsed from the n8n-generated markdown in the Obsidian vault). Both wrap
the same fetch/parse/cache functions the HTTP routes use
(routes/ithaca_routes.py is the single source of truth for that logic) so
the agent and the UI tile always see identical, identically-cached data.
"""

import logging
from typing import Dict, Optional

from src.tools._common import _parse_tool_args

logger = logging.getLogger(__name__)


async def do_get_home_weather(content: str, owner: Optional[str] = None) -> Dict:
    """Handle get_home_weather: current conditions + hourly forecast for
    the user's configured home location (Settings > Integrations, or the
    OPENWEATHER_LAT/LON env vars)."""
    from fastapi import HTTPException
    from routes.ithaca_routes import _cached, _fetch_weather, _weather_cache, _weather_lock, WEATHER_CACHE_TTL, _weather_settings

    try:
        try:
            args = _parse_tool_args(content)
        except ValueError:
            args = {}
        refresh = bool(args.get("refresh"))

        wcfg = _weather_settings()
        key = "|".join((wcfg["lat"], wcfg["lon"], wcfg["units"]))
        try:
            data = await _cached(_weather_cache, _weather_lock, key, WEATHER_CACHE_TTL, _fetch_weather, refresh)
        except HTTPException as e:
            return {"error": e.detail, "exit_code": 1}

        unit_t = "°F" if data.get("units") == "imperial" else "°C"
        cur = data.get("current") or {}

        def _r(v):
            return round(v) if isinstance(v, (int, float)) else v

        lines = [
            f"Current weather in {data.get('location') or 'your area'}: {cur.get('description', '')}, "
            f"{_r(cur.get('temp'))}{unit_t} (feels {_r(cur.get('feels_like'))}{unit_t}), "
            f"{cur.get('humidity', '—')}% humidity, wind {cur.get('wind_speed', '—')}."
        ]
        hourly = data.get("hourly") or []
        if hourly:
            lines.append("Upcoming hours:")
            for h in hourly:
                pop = f", {h['pop']}% chance of rain" if (h.get("pop") or 0) >= 20 else ""
                lines.append(f"- {int(h.get('local_hour', 0)):02d}:00 — {h.get('description', '')}, {_r(h.get('temp'))}{unit_t}{pop}")
        return {"response": "\n".join(lines), "weather": data, "exit_code": 0}
    except Exception as e:
        logger.error(f"get_home_weather error: {e}")
        return {"error": str(e), "exit_code": 1}


async def do_get_homelab_updates(content: str, owner: Optional[str] = None) -> Dict:
    """Handle get_homelab_updates: the latest daily-digest's parsed
    Software Updates table (per-app current/available version + status)."""
    from fastapi import HTTPException
    from routes.ithaca_routes import _cached, _fetch_digest, _digest_cache, _digest_lock, DIGEST_CACHE_TTL, _obsidian_settings

    try:
        try:
            args = _parse_tool_args(content)
        except ValueError:
            args = {}
        refresh = bool(args.get("refresh"))

        cfg = _obsidian_settings()
        key = f"{cfg['base']}|{cfg['digest_dir']}"
        try:
            data = await _cached(_digest_cache, _digest_lock, key, DIGEST_CACHE_TTL, _fetch_digest, refresh)
        except HTTPException as e:
            return {"error": e.detail, "exit_code": 1}

        su = data.get("software_updates") or {}
        rows = su.get("rows") or []
        date = data.get("date") or "latest"
        if not rows:
            response = su.get("note") or "No Software Updates section found in the latest digest."
            return {"response": response, "rows": [], "note": su.get("note", ""), "date": date, "exit_code": 0}

        flagged = [r for r in rows if r.get("update_available")]
        lines = [f"Software updates from the {date} digest ({len(flagged)} of {len(rows)} apps need an update):"]
        for r in rows:
            status = "⚠ update available" if r.get("update_available") else "up to date"
            version = r.get("version") or ""
            if r.get("update_available") and version and version != "—":
                ver_text = f"{r.get('deployed', '?')} → {version}"
            else:
                ver_text = r.get("deployed", "?")
            line = f"- {r.get('app', '?')} ({status}): {ver_text}"
            if r.get("desc"):
                line += f" — {r['desc']}"
            lines.append(line)
        if su.get("note"):
            lines.append(su["note"])
        return {"response": "\n".join(lines), "rows": rows, "note": su.get("note", ""), "date": date, "exit_code": 0}
    except Exception as e:
        logger.error(f"get_homelab_updates error: {e}")
        return {"error": str(e), "exit_code": 1}
