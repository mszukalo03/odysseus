"""
extensions/ithaca/backend.py

Ithaca hub — backend for the dashboard/homepage tile screen. Route wiring
only; each concern lives in its own module:

  extensions/ithaca/weather.py         — the one persistent built-in tile
  extensions/ithaca/tile_schema.py     — the portable tile config contract
  extensions/ithaca/tiles.py           — tile CRUD, query execution, layout
  extensions/ithaca/ai_tile_builder.py — single-shot LLM tile proposal
  extensions/ithaca/tile_packaging.py  — tile export/import

Endpoints:

* GET  /api/ithaca/weather   — live OpenWeatherMap current conditions +
  3-hourly forecast, TTL-cached.
* GET  /api/ithaca/tiles              — list user-defined tile configs.
* GET  /api/ithaca/tiles/{id}/data    — run a saved tile's query (TTL-cached,
  ?refresh=1 bypass) against its configured external Postgres connection
  (core/external_db.py) and return data shaped for its viz type.
* POST /api/ithaca/tiles/{id}/actions/{action_id}/run — fire one of a tile's
  action buttons (core/webhook_action.py) — same access tier as viewing the
  tile's data, since it's a pre-wired, admin-approved action.
* POST/DELETE /api/ithaca/tiles[/{id}] — admin-only: create/update/delete a
  tile config.
* POST /api/ithaca/tiles/preview      — admin-only: run an unsaved draft
  config for the tile-builder UI.
* POST /api/ithaca/tiles/ai-propose   — admin-only: single-shot LLM tile
  proposal from a connection + free-form context doc + NL instruction.
  Optional `current_config` {title,query,viz_type} asks the model to revise
  an existing tile instead of proposing one from scratch (used by the
  builder's Edit flow). Returns an unsaved draft config.
* GET  /api/ithaca/tiles/{id}/package.json — admin-only: download a portable
  tile package — the config plus a non-secret connection hint, never
  credentials.
* POST /api/ithaca/tiles/import       — admin-only: import a package,
  requiring an explicit local connection_binding and, if the tile has
  action buttons, an action_bindings map (never auto-matched).
* GET  /api/ithaca/layout              — grid placement (col/row/w/h) for
  every tile, built-in or user-defined.
* PUT  /api/ithaca/layout/{id}         — admin-only: persist a drag/resize.

Also exposed to the AI agent as read-only tools (src/tools/ithaca.py):
get_home_weather, query_ithaca_tile.

Config: OPENWEATHER_API_KEY, OPENWEATHER_LAT, OPENWEATHER_LON,
OPENWEATHER_UNITS (see .env.example) — or the same keys settable in
Settings > Integrations ("Ithaca Hub" card), which take priority over the
env vars when non-empty (see weather.py's `_setting_or_env`).
"""

import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin, reject_cross_site as _reject_cross_site
from core.external_db import ExternalDbError
from core.webhook_action import WebhookActionError

from extensions.ithaca import tiles as _tiles
from extensions.ithaca import weather as _weather
from extensions.ithaca.ai_tile_builder import propose_tile_config, TileProposalError
from extensions.ithaca.tile_packaging import build_tile_package, install_tile_package, TilePackagingError
from src.constants import TILE_IMPORT_MAX_BYTES

logger = logging.getLogger(__name__)

# Bearer-token callers need this scope (see routes/api_token_routes.py).
# Cookie-session callers already passed AuthMiddleware, so they go through
# untouched — same split the feeds routes use.
ITHACA_READ_SCOPES = {"ithaca:read"}


def _require_read_access(request: Request) -> None:
    """Gate for `ody_` bearer tokens: require the ithaca:read scope. Browser
    cookie sessions (and no-auth/single-user mode) pass — AuthMiddleware
    already rejected unauthenticated non-exempt requests before this runs."""
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(ITHACA_READ_SCOPES):
            raise HTTPException(403, "API token missing required scope: ithaca:read")


def setup() -> APIRouter:
    router = APIRouter(prefix="/api/ithaca", tags=["ithaca"])

    @router.get("/weather")
    async def get_weather(request: Request, refresh: bool = False):
        _require_read_access(request)
        try:
            return await _weather.get_weather(refresh)
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"OpenWeatherMap unreachable ({exc.__class__.__name__}): {exc}")

    # ─── User-defined tiles ──────────────────────────────────────────────

    @router.get("/tiles")
    async def list_tiles(request: Request):
        _require_read_access(request)
        return {"tiles": _tiles.list_tile_configs()}

    @router.get("/tiles/{tile_id}/data")
    async def get_tile_data(tile_id: str, request: Request, refresh: bool = False):
        _require_read_access(request)
        try:
            return await _tiles.run_tile(tile_id, force=refresh)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except ExternalDbError as exc:
            raise HTTPException(502, str(exc))

    @router.post("/tiles/{tile_id}/actions/{action_id}/run")
    async def run_tile_action(tile_id: str, action_id: str, request: Request):
        # Same access tier as viewing the tile's data (not admin) — an action
        # button is a pre-wired, admin-approved side effect the tile's own
        # viewers are meant to click; creating/editing the underlying webhook
        # target is what's admin-gated (routes/webhook_action_routes.py).
        _require_read_access(request)
        _reject_cross_site(request)
        try:
            return await _tiles.run_tile_action(tile_id, action_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except WebhookActionError as exc:
            raise HTTPException(400, str(exc))

    @router.post("/tiles")
    async def save_tile(request: Request):
        require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        try:
            return {"ok": True, "tile": _tiles.save_tile_config(body)}
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @router.delete("/tiles/{tile_id}")
    async def remove_tile(tile_id: str, request: Request):
        require_admin(request)
        _reject_cross_site(request)
        ok = _tiles.delete_tile_config(tile_id)
        _tiles.delete_tile_layout(tile_id)  # layout entries have no meaning once the tile is gone
        return {"ok": ok}

    @router.post("/tiles/preview")
    async def preview_tile(request: Request):
        require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        try:
            return await _tiles.preview_tile(body)
        except ExternalDbError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @router.post("/tiles/ai-propose")
    async def ai_propose_tile(request: Request):
        # Single-shot LLM proposal (extensions/ithaca/ai_tile_builder.py) — a
        # dedicated backend call, not the general agent tool loop (see that
        # module's docstring for why). Admin-only: this can run arbitrary
        # SELECTs against an external DB, same bar as saving a tile by hand.
        require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        tile_id = str(body.get("id") or "").strip()
        connection_ref = str(body.get("connection_ref") or "").strip()
        instruction = str(body.get("instruction") or "")
        context_doc = str(body.get("context_doc") or "")
        current_config = body.get("current_config")
        if not tile_id or not connection_ref:
            raise HTTPException(400, "id and connection_ref are required")
        from src.auth_helpers import effective_user
        try:
            return await propose_tile_config(
                tile_id, connection_ref, instruction, context_doc,
                owner=effective_user(request),
                current_config=current_config if isinstance(current_config, dict) else None,
            )
        except TileProposalError as exc:
            raise HTTPException(400, str(exc))

    # ─── Tile export/import packaging (extensions/ithaca/tile_packaging.py) ─

    @router.get("/tiles/{tile_id}/package.json")
    async def download_tile_package(tile_id: str, request: Request):
        # A tile's SQL query and viz config are the same sensitivity as the
        # tile itself (admin-only to create) — no credentials are ever in
        # the package, but the query text may reference internal hostnames.
        require_admin(request)
        try:
            return build_tile_package(tile_id)
        except TilePackagingError as exc:
            raise HTTPException(404, str(exc))

    @router.post("/tiles/import")
    async def import_tile_package(request: Request):
        require_admin(request)
        _reject_cross_site(request)
        raw = await request.body()
        if len(raw) > TILE_IMPORT_MAX_BYTES:
            raise HTTPException(413, f"Import payload exceeds the {TILE_IMPORT_MAX_BYTES}-byte tile package limit")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(400, "Request body is not valid JSON")
        package = body.get("package")
        connection_binding = body.get("connection_binding")
        action_bindings = body.get("action_bindings")
        if not isinstance(package, dict):
            raise HTTPException(400, "package must be a JSON object")
        try:
            return {"ok": True, "tile": install_tile_package(package, connection_binding, action_bindings)}
        except TilePackagingError as exc:
            raise HTTPException(400, str(exc))

    # ─── Grid layout (drag/resize) — separate from tile content configs so ──
    # ─── it also covers the built-in Weather tile.                         ──

    @router.get("/layout")
    async def get_layout(request: Request):
        _require_read_access(request)
        return {"layout": _tiles.load_layout()}

    @router.put("/layout/{tile_id}")
    async def put_layout(tile_id: str, request: Request):
        require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        return {"ok": True, "layout": _tiles.save_tile_layout(tile_id, body)}

    return router
