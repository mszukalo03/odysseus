"""
extensions/ithaca/tiles.py

CRUD + query-execution glue for user-defined Ithaca dashboard tiles — kept
separate from weather.py (the one built-in tile) so this module owns
everything that's generic to *any* tile config, regardless of what Postgres
connection/table it points at.

Storage: one JSON file per tile under data/ithaca/tiles/<id>.json
(atomic_write_json) — no DB table needed at this scale, and it keeps a
single tile trivially exportable (tile_packaging.py just reads the file).
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from core.external_db import run_readonly_query, ExternalDbError
from core.ttl_cache import TTLCache

from extensions.ithaca.tile_schema import TileConfig

logger = logging.getLogger(__name__)

TILES_DIR = os.path.join(DATA_DIR, "ithaca", "tiles")

# Grid placement (col/row/w/h, in the dashboard's grid track units) is kept
# separate from tile *content* configs — it applies to the built-in Weather
# tile too (id "weather"), which has no config JSON of its own to attach a
# layout field to.
LAYOUT_FILE = os.path.join(DATA_DIR, "ithaca", "layout.json")

# Per-tile TTL cache, single-flight (see core/ttl_cache.py) — each tile is
# cached independently, keyed by its own id.
_tile_cache = TTLCache()


def _tile_path(tile_id: str) -> str:
    return os.path.join(TILES_DIR, f"{tile_id}.json")


def list_tile_configs() -> list[dict]:
    if not os.path.isdir(TILES_DIR):
        return []
    out = []
    for path in sorted(glob.glob(os.path.join(TILES_DIR, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception as exc:
            logger.warning("Failed to read tile config %s: %s", path, exc)
    return out


def get_tile_config(tile_id: str) -> dict | None:
    path = _tile_path(tile_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tile_config(data: dict) -> dict:
    """Validate against TileConfig and persist. Returns the normalized dict."""
    cfg = TileConfig.model_validate(data)
    atomic_write_json(_tile_path(cfg.id), cfg.model_dump(), indent=2)
    _tile_cache.invalidate(cfg.id)
    return cfg.model_dump()


def delete_tile_config(tile_id: str) -> bool:
    path = _tile_path(tile_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    _tile_cache.invalidate(tile_id)
    return True


def load_layout() -> dict:
    if not os.path.exists(LAYOUT_FILE):
        return {}
    try:
        with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to read %s: %s", LAYOUT_FILE, exc)
        return {}


def save_tile_layout(tile_id: str, layout: dict) -> dict:
    """Persist one tile's grid placement. col/row are 1-based grid line
    starts, w/h are span counts — all clamped to >=1 so a malformed drag
    payload can't produce a zero/negative-size or off-grid tile."""
    data = load_layout()
    entry = {
        "col": max(1, int(layout.get("col", 1))),
        "row": max(1, int(layout.get("row", 1))),
        "w": max(1, int(layout.get("w", 1))),
        "h": max(1, int(layout.get("h", 1))),
    }
    data[tile_id] = entry
    atomic_write_json(LAYOUT_FILE, data, indent=2)
    return entry


def delete_tile_layout(tile_id: str) -> None:
    data = load_layout()
    if tile_id in data:
        del data[tile_id]
        atomic_write_json(LAYOUT_FILE, data, indent=2)


def _shape_rows(cfg: TileConfig, columns: list[str], rows: list[list]) -> dict:
    """Turn raw {columns, rows} into whatever shape the tile's viz.type
    needs on the frontend."""
    dict_rows = [dict(zip(columns, r)) for r in rows]
    viz = cfg.viz
    if viz.type in ("bar", "line", "pie", "stat"):
        label_field = viz.label_field or (columns[0] if columns else None)
        value_field = viz.value_field or (columns[1] if len(columns) > 1 else None)
        points = []
        for row in dict_rows:
            label = row.get(label_field) if label_field else None
            value = row.get(value_field) if value_field else None
            try:
                value = float(value) if value is not None else None
            except (TypeError, ValueError):
                value = None
            points.append({"label": str(label) if label is not None else "", "value": value})
        if viz.type == "stat":
            total = points[0]["value"] if points else None
            return {"type": "stat", "value": total}
        return {"type": viz.type, "points": points}
    if viz.type == "list":
        primary = columns[0] if columns else None
        return {"type": "list", "items": [str(row.get(primary, "")) for row in dict_rows]}
    # table (default)
    return {"type": "table", "columns": columns, "rows": dict_rows}


def _run_tile_sync(cfg: TileConfig) -> dict:
    result = run_readonly_query(cfg.data_source.connection_ref, cfg.data_source.query)
    return _shape_rows(cfg, result["columns"], result["rows"])


async def run_tile(tile_id: str, force: bool = False) -> dict:
    """Resolve + run a saved tile config, TTL-cached (see core/ttl_cache.py)
    per its own refresh_interval_seconds. Raises ExternalDbError on query
    failure or ValueError if the tile config doesn't exist."""
    data = get_tile_config(tile_id)
    if data is None:
        raise ValueError(f"No tile config with id '{tile_id}'")
    cfg = TileConfig.model_validate(data)

    if force:
        _tile_cache.invalidate(tile_id)
    return await _tile_cache.get(
        tile_id, cfg.refresh_interval_seconds,
        lambda: asyncio.to_thread(_run_tile_sync, cfg),
    )


async def preview_tile(data: dict) -> dict:
    """Run an *unsaved* draft config for the builder UI — validated, not
    persisted, not cached."""
    cfg = TileConfig.model_validate(data)
    return await asyncio.to_thread(_run_tile_sync, cfg)


async def run_tile_action(tile_id: str, action_id: str) -> dict:
    """Fire one of a saved tile's action buttons (core/webhook_action.py).
    Never cached — an action button click is a one-shot side effect, not a
    data fetch. Raises ValueError if the tile or action doesn't exist."""
    from core.webhook_action import run_webhook_action

    data = get_tile_config(tile_id)
    if data is None:
        raise ValueError(f"No tile config with id '{tile_id}'")
    cfg = TileConfig.model_validate(data)

    action = next((a for a in cfg.actions if a.id == action_id), None)
    if action is None:
        raise ValueError(f"Tile '{tile_id}' has no action '{action_id}'")

    return await run_webhook_action(
        action.endpoint_ref, method_override=action.method, path=action.path, body=action.body,
    )
