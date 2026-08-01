"""Ithaca-domain tool implementations.

Read-only agent access to the Ithaca hub's tiles:
- Weather: local weather, queried directly from OpenWeatherMap
  (extensions/ithaca/weather.py — same cache the HTTP route uses, so the
  agent and the UI tile always see identical data).
- query_ithaca_tile: any user-defined dashboard tile (extensions/ithaca/tiles.py)
  — each backed by a live query against a database the user connected. This
  one tool covers every custom tile without needing a bespoke tool per tile:
  list them, then run one, and its live data comes back as text.
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
    from extensions.ithaca.weather import get_weather

    try:
        try:
            args = _parse_tool_args(content)
        except ValueError:
            args = {}
        refresh = bool(args.get("refresh"))

        try:
            data = await get_weather(refresh)
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


def _format_tile_data(tile_id: str, data: dict) -> str:
    """Render a tile's shaped data (extensions/ithaca/tiles.py's
    _shape_rows output) as plain text for the agent to reason over."""
    kind = data.get("type")
    if kind == "table":
        columns = data.get("columns") or []
        rows = data.get("rows") or []
        if not rows:
            return f"Tile '{tile_id}' returned no rows."
        lines = [f"Tile '{tile_id}' ({len(rows)} row{'s' if len(rows) != 1 else ''}):"]
        lines += ["- " + ", ".join(f"{c}={row.get(c)}" for c in columns) for row in rows]
        return "\n".join(lines)
    if kind == "list":
        items = data.get("items") or []
        return f"Tile '{tile_id}': " + (", ".join(items) if items else "(no items)")
    if kind == "stat":
        return f"Tile '{tile_id}': {data.get('value')}"
    if kind in ("bar", "line", "pie"):
        points = data.get("points") or []
        if not points:
            return f"Tile '{tile_id}' returned no data points."
        lines = [f"Tile '{tile_id}':"]
        lines += [f"- {p.get('label')}: {p.get('value')}" for p in points]
        return "\n".join(lines)
    return f"Tile '{tile_id}': {data}"


async def do_query_ithaca_tile(content: str, owner: Optional[str] = None) -> Dict:
    """Handle query_ithaca_tile: list the user's custom Ithaca dashboard
    tiles (no tile_id), or run one and return its live data (tile_id given).
    Every tile is a saved, already-vetted read-only query — this tool never
    accepts arbitrary SQL from the agent, only a tile id to run."""
    from extensions.ithaca.tiles import list_tile_configs, run_tile
    from core.external_db import ExternalDbError

    try:
        args = _parse_tool_args(content)
    except ValueError:
        args = {}
    tile_id = str(args.get("tile_id") or "").strip()

    try:
        if not tile_id:
            tiles = list_tile_configs()
            if not tiles:
                return {"response": "No custom Ithaca tiles configured yet.", "tiles": [], "exit_code": 0}
            lines = ["Custom Ithaca tiles (call query_ithaca_tile again with a tile_id to run one):"]
            for t in tiles:
                note = f" — {t['notes']}" if t.get("notes") else ""
                lines.append(f"- {t['id']}: {t['title']}{note}")
            return {"response": "\n".join(lines), "tiles": tiles, "exit_code": 0}

        try:
            data = await run_tile(tile_id)
        except ValueError as e:
            return {"error": str(e), "exit_code": 1}
        except ExternalDbError as e:
            return {"error": f"Query failed: {e}", "exit_code": 1}
        return {"response": _format_tile_data(tile_id, data), "data": data, "exit_code": 0}
    except Exception as e:
        logger.error(f"query_ithaca_tile error: {e}")
        return {"error": str(e), "exit_code": 1}
