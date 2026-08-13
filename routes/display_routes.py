"""Per-feature display defaults — does a nav-shell feature render as a
full-canvas page or as a popup window?

The stored map lives in the core settings KV (`feature_display_modes` in
`src/settings.py`), not in per-user prefs: the choice describes how the app's
chrome is laid out, which is the same class of decision as the sidebar/rail
settings that already live there.

Reads are open to any authed caller — `static/js/workspaceManager.js` fetches
the whole map once at boot to decide how to open each registered workspace, and
that has to work for non-admins too or the nav shell would render differently
depending on who is logged in. Writes are admin-or-single-user, matching
`POST /api/settings`.
"""
from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import get_current_user
from src.settings import (
    FEATURE_DISPLAY_MODES,
    get_feature_display_mode,
    get_feature_display_modes,
    set_feature_display_mode,
)
from src.tool_security import owner_is_admin_or_single_user


def setup_display_routes():
    router = APIRouter(prefix="/api/display-modes", tags=["display"])

    def _require_writer(request: Request) -> str:
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(403, "Changing feature display defaults is admin-only")
        return owner

    @router.get("")
    async def list_display_modes():
        """The full {feature_id: mode} map, defaults merged in."""
        return {"modes": get_feature_display_modes(), "allowed": list(FEATURE_DISPLAY_MODES)}

    @router.get("/{feature_id}")
    async def read_display_mode(feature_id: str):
        """One feature's mode. Absent ids resolve to "page" rather than 404 —
        the caller is asking "how should I render this", and the nav shell's
        answer for an unconfigured feature is the page default."""
        return {"feature_id": feature_id, "mode": get_feature_display_mode(feature_id)}

    @router.put("/{feature_id}")
    async def update_display_mode(request: Request, feature_id: str, body: dict):
        _require_writer(request)
        mode = (body or {}).get("mode")
        try:
            modes = set_feature_display_mode(feature_id, mode)
        except ValueError as err:
            raise HTTPException(400, str(err))
        return {"feature_id": feature_id, "mode": modes[feature_id], "modes": modes}

    return router
