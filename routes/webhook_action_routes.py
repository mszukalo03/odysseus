"""
routes/webhook_action_routes.py

Admin-only CRUD for `WebhookTarget` rows (core/database.py) — HTTP endpoints
an Ithaca tile action button can hit (e.g. an n8n webhook that reprocesses
something). Execution lives in core/webhook_action.py; this module is just
the target CRUD + test + fire HTTP surface.

Entirely admin-gated for CRUD: a target's URL/auth token are the
highest-value secret this feature introduces. Firing a configured action
(POST /{id}/run) is exposed separately with a lower bar, matching tile-data
read access — see extensions/ithaca/backend.py's tile-action route, which is
the actual button-click endpoint end users hit.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, WebhookTarget
from core.middleware import require_admin, reject_cross_site
from core.webhook_action import test_target, ALLOWED_METHODS

logger = logging.getLogger(__name__)


class WebhookTargetCreate(BaseModel):
    label: str
    url: str
    method: str = "POST"
    auth_scheme: str = "none"
    auth_token: str = ""
    timeout_seconds: int = 15


class WebhookTargetUpdate(BaseModel):
    label: str | None = None
    url: str | None = None
    method: str | None = None
    auth_scheme: str | None = None
    auth_token: str | None = None  # only overwritten when non-empty
    timeout_seconds: int | None = None


def _to_dict(row: WebhookTarget) -> dict:
    return {
        "id": row.id,
        "label": row.label,
        "url": row.url,
        "method": row.method,
        "auth_scheme": row.auth_scheme,
        "has_auth_token": bool(row.auth_token),
        "timeout_seconds": row.timeout_seconds,
    }


def _valid_method(method: str) -> str:
    m = (method or "POST").upper()
    if m not in ALLOWED_METHODS:
        raise HTTPException(400, f"method must be one of {sorted(ALLOWED_METHODS)}")
    return m


def _valid_auth_scheme(scheme: str) -> str:
    s = (scheme or "none").lower()
    if s not in ("none", "bearer", "basic"):
        raise HTTPException(400, "auth_scheme must be one of: none, bearer, basic")
    return s


def setup_webhook_action_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/webhook-targets", tags=["webhook-targets"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("")
    async def list_webhook_targets():
        db = SessionLocal()
        try:
            rows = db.query(WebhookTarget).order_by(WebhookTarget.label.asc()).all()
            return {"targets": [_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.post("")
    async def create_webhook_target(data: WebhookTargetCreate, request: Request):
        reject_cross_site(request)
        label = data.label.strip()
        if not label or not data.url.strip():
            return {"ok": False, "error": "label and url are required"}
        db = SessionLocal()
        try:
            row = WebhookTarget(
                id=uuid.uuid4().hex,
                label=label,
                url=data.url.strip(),
                method=_valid_method(data.method),
                auth_scheme=_valid_auth_scheme(data.auth_scheme),
                auth_token=data.auth_token or "",
                timeout_seconds=max(1, min(int(data.timeout_seconds or 15), 60)),
            )
            db.add(row)
            db.commit()
            return {"ok": True, "id": row.id}
        finally:
            db.close()

    @router.put("/{target_id}")
    async def update_webhook_target(target_id: str, data: WebhookTargetUpdate, request: Request):
        reject_cross_site(request)
        db = SessionLocal()
        try:
            row = db.get(WebhookTarget, target_id)
            if not row:
                return {"ok": False, "error": "Webhook target not found"}
            if data.label is not None:
                row.label = data.label.strip()
            if data.url is not None:
                row.url = data.url.strip()
            if data.method is not None:
                row.method = _valid_method(data.method)
            if data.auth_scheme is not None:
                row.auth_scheme = _valid_auth_scheme(data.auth_scheme)
            if data.auth_token:
                row.auth_token = data.auth_token
            if data.timeout_seconds is not None:
                row.timeout_seconds = max(1, min(int(data.timeout_seconds), 60))
            db.commit()
            return {"ok": True, "id": row.id}
        finally:
            db.close()

    @router.delete("/{target_id}")
    async def delete_webhook_target(target_id: str, request: Request):
        reject_cross_site(request)
        db = SessionLocal()
        try:
            row = db.get(WebhookTarget, target_id)
            if not row:
                return {"ok": False, "error": "Webhook target not found"}
            db.delete(row)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/{target_id}/test")
    async def test_webhook_target(target_id: str, request: Request):
        reject_cross_site(request)
        return test_target(target_id)

    return router
