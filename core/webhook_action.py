"""
core/webhook_action.py

Execution for admin-configured webhook targets (`WebhookTarget` in
core/database.py) — the HTTP side of an Ithaca tile action button
(extensions/ithaca/tile_schema.py's TileAction). Parallels core/external_db.py:
that module is the read-only-SQL trust boundary for tile *data*, this module
is the outbound-HTTP trust boundary for tile *actions*.

Safety model:
  1. Only http/https URLs are accepted (rejects file://, javascript:, etc. —
     not that anything else could reach here, but fail loudly if it did).
  2. Only GET/POST/PUT/PATCH are allowed — no arbitrary verb.
  3. A hard timeout (per-target, admin-set) so a hung webhook can't block
     the request indefinitely.
  4. The response body is truncated before being handed back to the caller —
     this is a status/log surface, not a data-fetch path.

This is a lower bar than core/external_db.py's defense-in-depth (no
"read-only" concept applies to an HTTP POST), because a webhook target is
inherently meant to trigger a side effect — the safety boundary is *which*
URL an admin was willing to configure, same trust level as an admin writing
a tile's SQL query.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from core.database import SessionLocal, WebhookTarget

logger = logging.getLogger(__name__)

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH"}
MAX_RESPONSE_BODY_CHARS = 4000


class WebhookActionError(Exception):
    """User-facing error for a failed webhook target lookup/call."""


def get_target(target_id: str) -> WebhookTarget:
    db = SessionLocal()
    try:
        target = db.get(WebhookTarget, target_id)
        if not target:
            raise WebhookActionError(f"No webhook endpoint configured with id '{target_id}'")
        db.expunge(target)
        return target
    finally:
        db.close()


def list_targets() -> list[WebhookTarget]:
    db = SessionLocal()
    try:
        rows = db.query(WebhookTarget).order_by(WebhookTarget.label.asc()).all()
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def _auth_headers(target: WebhookTarget) -> dict:
    if target.auth_scheme == "bearer" and target.auth_token:
        return {"Authorization": f"Bearer {target.auth_token}"}
    if target.auth_scheme == "basic" and target.auth_token:
        import base64
        encoded = base64.b64encode(target.auth_token.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {}


async def run_webhook_action(
    target_id: str, method_override: Optional[str] = None,
    path: Optional[str] = None, body: Optional[dict] = None,
) -> dict[str, Any]:
    """Fire a configured webhook target. Returns
    {"ok": bool, "status_code": int|None, "response_text": str, "error": str|None} —
    never raises for an HTTP-level failure (4xx/5xx/timeout/connect-error),
    only for a misconfigured/missing target (WebhookActionError)."""
    target = get_target(target_id)

    method = (method_override or target.method or "POST").upper()
    if method not in ALLOWED_METHODS:
        raise WebhookActionError(f"Method '{method}' is not allowed — use one of {sorted(ALLOWED_METHODS)}")

    url = target.url
    if path:
        url = url.rstrip("/") + "/" + path.lstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookActionError(f"Refusing to call non-HTTP(S) URL: {url!r}")

    headers = _auth_headers(target)
    timeout = max(1, min(int(target.timeout_seconds or 15), 60))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers, json=body if body else None)
        text = resp.text[:MAX_RESPONSE_BODY_CHARS]
        return {"ok": resp.is_success, "status_code": resp.status_code, "response_text": text, "error": None}
    except httpx.TimeoutException:
        logger.warning("Webhook target %s timed out after %ss", target_id, timeout)
        return {"ok": False, "status_code": None, "response_text": "", "error": f"Timed out after {timeout}s"}
    except httpx.HTTPError as exc:
        logger.warning("Webhook target %s failed: %s", target_id, exc)
        return {"ok": False, "status_code": None, "response_text": "", "error": str(exc)}


def _extract_rows(payload: Any, json_path: Optional[str]) -> list[dict]:
    """Walk `json_path` (dot-separated keys) into a decoded JSON response to
    find the array of row objects. No json_path means the response body
    itself must be that array — the common case for a plain REST list
    endpoint like GET /api/reactions."""
    node = payload
    if json_path:
        for part in json_path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise WebhookActionError(f"json_path '{json_path}' not found in response (missing '{part}')")
            node = node[part]
    if not isinstance(node, list):
        where = f" at json_path '{json_path}'" if json_path else ""
        raise WebhookActionError(f"Response is not a JSON array of objects{where}")
    return node


async def fetch_tile_data(
    target_id: str, path: Optional[str] = None,
    query_params: Optional[dict[str, str]] = None,
    json_path: Optional[str] = None, row_limit: int = 500,
) -> dict[str, Any]:
    """GET a configured webhook target for tile *data* — the read-side
    counterpart to run_webhook_action's fire-and-log action-button use.
    Reshapes the JSON response into the same {"columns", "rows", "truncated"}
    contract core/external_db.py's run_readonly_query returns, so
    extensions/ithaca/tiles.py's _shape_rows treats an HTTP tile identically
    to a SQL one. Always GETs regardless of the target's configured method —
    a data source never has a side effect. Raises WebhookActionError on any
    failure (network, non-2xx, bad JSON shape); unlike run_webhook_action
    this result is consumed as data rather than shown as a status, so a
    failure can't be swallowed into an {"ok": false} dict."""
    target = get_target(target_id)

    url = target.url
    if path:
        url = url.rstrip("/") + "/" + path.lstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookActionError(f"Refusing to call non-HTTP(S) URL: {url!r}")

    headers = _auth_headers(target)
    timeout = max(1, min(int(target.timeout_seconds or 15), 60))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params=query_params or None)
    except httpx.TimeoutException:
        raise WebhookActionError(f"Timed out after {timeout}s")
    except httpx.HTTPError as exc:
        raise WebhookActionError(str(exc)) from exc

    if not resp.is_success:
        raise WebhookActionError(f"Request failed with status {resp.status_code}: {resp.text[:200]}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise WebhookActionError(f"Response was not valid JSON: {exc}") from exc

    rows = _extract_rows(payload, json_path)
    truncated = len(rows) > row_limit
    rows = rows[:row_limit]

    columns: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise WebhookActionError("Each item in the response array must be a JSON object")
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    row_lists = [[row.get(c) for c in columns] for row in rows]
    return {"columns": columns, "rows": row_lists, "truncated": truncated}


def test_target(target_id: str) -> dict[str, Any]:
    """Cheap reachability check for the Settings UI's Test button — a plain
    GET/HEAD-ish probe is not always meaningful for a webhook (many only
    accept POST), so this just resolves the target and reports whether it
    was found + its configured method/URL rather than actually firing it."""
    try:
        target = get_target(target_id)
        return {"ok": True, "url": target.url, "method": target.method, "error": None}
    except WebhookActionError as exc:
        return {"ok": False, "error": str(exc)}
