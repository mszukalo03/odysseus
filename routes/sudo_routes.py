"""Sudo password prompt endpoints for agent shell commands.

The agent's bash subprocess has no TTY, so `sudo` can't prompt for a password
itself. When a command needs root, the tool blocks and pushes a `sudo_prompt`
event down the chat SSE stream; the browser collects the password and posts it
back here, which unblocks the waiting command.

The password is handed straight to the in-memory broker (`src.sudo_auth`) and
never persisted, logged, echoed back in a response, or shown to the model.

Note on exposure: the password crosses the same local HTTP connection as the
rest of the app's auth. That's fine for the default loopback bind — if you
expose Odysseus beyond localhost, terminate it behind TLS like any other
credential-bearing endpoint.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from src import sudo_auth
from src.auth_helpers import effective_user
from routes.email_helpers import require_user

logger = logging.getLogger(__name__)


def _authed_owner(request: Request) -> str:
    """401 unless authenticated; returns the same owner key the agent loop uses."""
    require_user(request)
    return effective_user(request) or ""


def setup_sudo_routes() -> APIRouter:
    router = APIRouter(tags=["sudo"])

    @router.get("/api/agent/sudo/status")
    async def sudo_status(request: Request) -> Dict[str, Any]:
        """Whether a prompt is in flight and whether a password is cached.

        Lets the UI re-attach to a pending prompt after a refresh. Never
        includes the password.
        """
        owner = _authed_owner(request)
        return {
            "pending": sudo_auth.pending_for(owner),
            "cached": sudo_auth.has_cached(owner),
        }

    @router.post("/api/agent/sudo/password")
    async def submit_sudo_password(request: Request) -> Dict[str, Any]:
        """Hand a password to the blocked command."""
        owner = _authed_owner(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Expected a JSON body")
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object")

        request_id = (body.get("request_id") or "").strip()
        password = body.get("password") or ""
        remember = bool(body.get("remember", True))
        if not request_id:
            raise HTTPException(400, "request_id is required")
        if not password:
            raise HTTPException(400, "password is required")

        ok = sudo_auth.submit(owner, request_id, password, remember=remember)
        if not ok:
            # Stale/unknown id — the prompt already timed out or was cancelled.
            raise HTTPException(409, "No matching sudo prompt is waiting")
        # Deliberately no echo of the submitted value.
        logger.info("sudo password supplied for a pending agent command")
        return {"success": True}

    @router.post("/api/agent/sudo/cancel")
    async def cancel_sudo_prompt(request: Request) -> Dict[str, Any]:
        """Dismiss the prompt; the waiting command fails cleanly instead of hanging."""
        owner = _authed_owner(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        request_id = ((body or {}).get("request_id") or "").strip()
        if not request_id:
            raise HTTPException(400, "request_id is required")
        return {"success": sudo_auth.cancel(owner, request_id)}

    @router.post("/api/agent/sudo/forget")
    async def forget_sudo_password(request: Request) -> Dict[str, Any]:
        """Drop the cached password before its TTL expires."""
        owner = _authed_owner(request)
        sudo_auth.clear(owner)
        return {"success": True}

    return router
