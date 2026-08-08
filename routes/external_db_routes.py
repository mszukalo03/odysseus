"""
routes/external_db_routes.py

Admin-only CRUD for `ExternalDbConnection` rows (core/database.py) —
Postgres/MySQL/SQLite connections to databases this app doesn't own (e.g. a
homelab automation's own DB, or a standalone SQLite file), used as read-only
data sources for Ithaca dashboard tiles (extensions/ithaca/tiles.py). Query
execution, read-only enforcement, and per-dialect detail live in
core/external_db.py + core/db_dialects.py; this module is just the
connection CRUD + test + introspect HTTP surface.

Entirely admin-gated: a connection's host/port/credentials (or SQLite file
path) are the highest-value secret this feature introduces, so nothing here
is exposed to non-admin users, and passwords are never included in list/get
responses.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, ExternalDbConnection
from core.middleware import require_admin, reject_cross_site
from core.external_db import test_connection, introspect_schema, invalidate_schema_cache, ExternalDbError
from core.db_dialects import get_adapter, SUPPORTED_KINDS, UnsupportedDialectError

logger = logging.getLogger(__name__)


class DbConnectionCreate(BaseModel):
    label: str
    kind: str = "postgres"
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = ""
    sslmode: str = "prefer"
    file_path: str = ""


class DbConnectionUpdate(BaseModel):
    label: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None  # only overwritten when non-empty, like EmailAccount
    sslmode: str | None = None
    file_path: str | None = None
    # kind is intentionally NOT updatable — changing it would silently
    # repoint every field's meaning (and any tile using this connection) at
    # a different backend. Create a new connection instead.


def _validate_conn_fields(kind: str, data) -> str | None:
    """Shared create/update validation. Returns an error string, or None."""
    if kind not in SUPPORTED_KINDS:
        return f"kind must be one of {', '.join(SUPPORTED_KINDS)}"
    if kind == "sqlite":
        file_path = (getattr(data, "file_path", "") or "").strip()
        if not file_path:
            return "file_path is required for sqlite connections"
    else:
        if not (data.host or "").strip() or not (data.database or "").strip() or not (data.username or "").strip():
            return "host, database, and username are required"
    return None


def _to_dict(row: ExternalDbConnection) -> dict:
    try:
        get_adapter(row.kind).require_driver()
        driver_available = True
        driver_hint = None
    except UnsupportedDialectError:
        driver_available = True  # unknown kind isn't a missing-driver problem
        driver_hint = None
    except Exception as exc:
        driver_available = False
        driver_hint = str(exc)
    return {
        "id": row.id,
        "label": row.label,
        "kind": row.kind,
        "host": row.host,
        "port": row.port,
        "database": row.database,
        "username": row.username,
        "has_password": bool(row.password),
        "sslmode": row.sslmode,
        "file_path": row.file_path,
        "read_only": bool(row.read_only),
        "driver_available": driver_available,
        "driver_hint": driver_hint,
    }


def setup_external_db_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/db-connections", tags=["db-connections"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/kinds")
    async def list_db_kinds():
        # Declared above /{conn_id} — otherwise FastAPI would match "kinds"
        # as a conn_id.
        kinds = []
        for kind in SUPPORTED_KINDS:
            adapter = get_adapter(kind)
            try:
                adapter.require_driver()
                driver_available, driver_hint = True, None
            except Exception as exc:
                driver_available, driver_hint = False, str(exc)
            fields = ["file_path"] if not adapter.uses_network else (
                ["host", "port", "database", "username", "password"]
                + (["sslmode"] if adapter.uses_sslmode else [])
            )
            kinds.append({
                "kind": kind,
                "default_port": adapter.default_port,
                "fields": fields,
                "driver_available": driver_available,
                "driver_hint": driver_hint,
            })
        return {"kinds": kinds}

    @router.get("")
    async def list_db_connections():
        db = SessionLocal()
        try:
            rows = db.query(ExternalDbConnection).order_by(ExternalDbConnection.label.asc()).all()
            return {"connections": [_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.post("")
    async def create_db_connection(data: DbConnectionCreate, request: Request):
        reject_cross_site(request)
        label = data.label.strip()
        if not label:
            return {"ok": False, "error": "label required"}
        kind = (data.kind or "postgres").strip().lower()
        err = _validate_conn_fields(kind, data)
        if err:
            return {"ok": False, "error": err}
        adapter = get_adapter(kind)
        db = SessionLocal()
        try:
            row = ExternalDbConnection(
                id=uuid.uuid4().hex,
                label=label,
                kind=kind,
                host=(data.host or "").strip(),
                port=int(data.port or adapter.default_port or 0),
                database=(data.database or "").strip(),
                username=(data.username or "").strip(),
                password=data.password or "",
                sslmode=(data.sslmode or "prefer").strip() if adapter.uses_sslmode else "",
                file_path=(data.file_path or "").strip(),
                read_only=True,
            )
            db.add(row)
            db.commit()
            return {"ok": True, "id": row.id}
        finally:
            db.close()

    @router.put("/{conn_id}")
    async def update_db_connection(conn_id: str, data: DbConnectionUpdate, request: Request):
        reject_cross_site(request)
        db = SessionLocal()
        try:
            row = db.get(ExternalDbConnection, conn_id)
            if not row:
                return {"ok": False, "error": "Connection not found"}
            for key in ("label", "host", "database", "username", "sslmode", "file_path"):
                val = getattr(data, key)
                if val is not None:
                    setattr(row, key, val.strip())
            if data.port is not None:
                row.port = int(data.port)
            if data.password:
                row.password = data.password
            db.commit()
            invalidate_schema_cache(conn_id)  # host/db/file_path may have changed under this id
            return {"ok": True, "id": row.id}
        finally:
            db.close()

    @router.delete("/{conn_id}")
    async def delete_db_connection(conn_id: str, request: Request):
        reject_cross_site(request)
        db = SessionLocal()
        try:
            row = db.get(ExternalDbConnection, conn_id)
            if not row:
                return {"ok": False, "error": "Connection not found"}
            db.delete(row)
            db.commit()
            invalidate_schema_cache(conn_id)
            return {"ok": True}
        finally:
            db.close()

    @router.post("/{conn_id}/test")
    async def test_db_connection(conn_id: str, request: Request):
        reject_cross_site(request)
        return test_connection(conn_id)

    @router.get("/{conn_id}/introspect")
    async def introspect_db_connection(conn_id: str):
        try:
            return {"tables": await introspect_schema(conn_id)}
        except ExternalDbError as exc:
            raise HTTPException(400, str(exc))

    return router
