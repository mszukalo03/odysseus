"""
extensions/argos/backend.py

Argos — browser companion bridge. Route wiring for the Chrome/Brave
extension (extensions/argos/browser/): pairing (mint a scoped token) and
per-turn page-context injection. The extension drives /api/chat_stream and
/api/companion/models directly with its own bearer token; this module never
proxies the chat turn itself (see extensions/README.md / the plan for why).

Endpoints:

* GET  /api/argos/hello    — cheap scope-gated pairing check.
* POST /api/argos/context  — wrap the active page's text as untrusted
  context and persist it into a chat session, deduped per session so a
  follow-up question on an unchanged page doesn't re-inject 10 KB.
* POST /api/argos/pair     — admin-cookie only: mint a token scoped for
  ["chat", "argos:ask"] and hand it back once.
* GET  /api/argos/browser-extension.zip — admin-only: download the MV3
  extension source (extensions/argos/browser/) as a zip for "Load unpacked".

Page text is attacker-controlled by definition. See page_context.py for the
untrusted-context wrapping, and routes/chat_routes.py's `no_tools` flag for
why the extension's chat turns run with agent tools/research/plan-mode off.
"""

import io
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)

# Bearer-token callers need this scope (see routes/api_token_routes.py).
# Cookie-session callers already passed AuthMiddleware, so they go through
# untouched — same split the feeds/ithaca routes use.
ARGOS_SCOPES = {"argos:ask"}

_EXTENSION_DIR = Path(__file__).resolve().parent
_BROWSER_DIR = _EXTENSION_DIR / "browser"


def _scope_owner(request: Request) -> Optional[str]:
    """Resolve the caller for Argos routes.

    Bearer-token callers must carry argos:ask; the returned owner is the
    token's real owner, never the "api" pseudo-user. Cookie-session (and
    single-user/no-auth mode) callers fall back to get_current_user — the
    auth middleware already gated unauthenticated access to non-exempt
    paths, so this preserves existing browser behavior. Mirrors
    extensions/rss/backend.py:_scope_owner.
    """
    from src.auth_helpers import get_current_user

    if getattr(request.state, "api_token", False):
        scopes = getattr(request.state, "api_token_scopes", None) or []
        if isinstance(scopes, str):
            scopes = [s.strip() for s in scopes.split(",")]
        scope_set = {str(s).strip() for s in scopes if str(s).strip()}
        if not scope_set.intersection(ARGOS_SCOPES):
            raise HTTPException(403, "API token missing required scope: argos:ask")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    return get_current_user(request)


def _build_browser_zip() -> bytes:
    """Zip extensions/argos/browser/ for Chrome "Load unpacked" (as a
    directory, once extracted) or direct re-hosting. Excludes the same junk
    src/extension_host.py:build_package_zip excludes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(_BROWSER_DIR.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                continue
            if path.name == ".DS_Store":
                continue
            zf.write(path, arcname=str(path.relative_to(_BROWSER_DIR)))
    return buf.getvalue()


def setup() -> APIRouter:
    router = APIRouter(prefix="/api/argos", tags=["argos"])

    @router.get("/hello")
    async def hello(request: Request) -> Dict[str, Any]:
        """Cheap, scope-validated pairing check for the extension's options
        page. A 200 confirms the server URL + token are both good."""
        from core.constants import APP_VERSION
        from extensions.argos.page_context import PAGE_TEXT_MAX_CHARS

        owner = _scope_owner(request)
        return {
            "ok": True,
            "name": "odysseus",
            "version": APP_VERSION,
            "owner": owner,
            "max_page_chars": PAGE_TEXT_MAX_CHARS,
        }

    @router.post("/context")
    async def inject_page_context(
        request: Request,
        session: str = Form(...),
        url: str = Form(""),
        title: str = Form(""),
        text: str = Form(""),
        selection: str = Form(""),
    ) -> Dict[str, Any]:
        from core.models import ChatMessage
        from extensions.argos.page_context import (
            PAGE_TEXT_HARD_CAP,
            build_page_context_message,
            is_duplicate_context,
            remember_context,
        )
        from routes.session_routes import _verify_session_owner

        _scope_owner(request)
        if len(text) > PAGE_TEXT_HARD_CAP or len(selection) > PAGE_TEXT_HARD_CAP:
            raise HTTPException(413, "Page content too large")
        _verify_session_owner(request, session)

        body_for_dedupe = (selection or text or "")
        if is_duplicate_context(session, body_for_dedupe):
            return {"status": "unchanged"}

        msg = build_page_context_message(url, title, text, selection)
        if msg is None:
            return {"status": "empty"}

        session_manager = request.app.state.session_manager
        try:
            sess = session_manager.get_session(session)
        except KeyError:
            raise HTTPException(404, "Session not found")
        sess.add_message(ChatMessage(msg["role"], msg["content"], metadata=msg.get("metadata")))
        session_manager.save_sessions()
        remember_context(session, body_for_dedupe)

        return {"status": "context_injected", "chars": len(msg["content"])}

    @router.post("/pair")
    async def pair(request: Request) -> Dict[str, Any]:
        """Mint a token scoped for the browser extension. Admin-cookie only,
        POST-only: SameSite=Lax means the session cookie isn't sent on a
        cross-site POST, so a malicious page can't trigger this the way it
        could a GET (see companion/routes.py's docstring for the same
        reasoning applied to /api/companion/pair). reject_cross_site is
        deliberately used only on this route — it would 403 the extension's
        own same-origin-exempt fetches on /hello and /context."""
        from core.middleware import reject_cross_site, require_admin
        from src.auth_helpers import get_current_user
        from companion import pairing as _pairing

        reject_cross_site(request)
        require_admin(request)
        owner = get_current_user(request)
        if not owner:
            raise HTTPException(400, "No admin user resolved for this session")

        token_id, raw_token = _pairing.mint_token(
            owner, name="browser extension", scopes=["chat", "argos:ask"]
        )
        invalidate = getattr(request.app.state, "invalidate_token_cache", None)
        if callable(invalidate):
            invalidate()

        return {"token": raw_token, "token_id": token_id, "scopes": ["chat", "argos:ask"]}

    @router.get("/browser-extension.zip")
    async def browser_extension_zip(request: Request) -> Response:
        from core.middleware import require_admin

        require_admin(request)
        if not _BROWSER_DIR.is_dir():
            raise HTTPException(404, "Browser extension source not found")
        data = _build_browser_zip()
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=argos-browser-extension.zip"},
        )

    return router
