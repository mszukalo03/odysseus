"""
routes/external_db_routes.py

Admin-only CRUD for `ExternalDbConnection` rows (core/database.py) — Postgres
connections to databases this app doesn't own (e.g. a homelab automation's
own DB), used as read-only data sources for Ithaca dashboard tiles
(extensions/ithaca/tiles.py). Query execution and read-only enforcement live
in core/external_db.py; this module is just the connection CRUD + test +
introspect HTTP surface.

Entirely admin-gated: a connection's host/port/credentials are the
highest-value secret this feature introduces, so nothing here is exposed to
non-admin users, and passwords are never included in list/get responses.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, ExternalDbConnection
from core.middleware import require_admin, reject_cross_site
from core.external_db import test_connection, introspect_schema, invalidate_schema_cache, ExternalDbError

logger = logging.getLogger(__name__)


class DbConnectionCreate(BaseModel):
    label: str
    host: str
    port: int = 5432
    database: str
    username: str
    password: str = ""
    sslmode: str = "prefer"


class DbConnectionUpdate(BaseModel):
    label: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None  # only overwritten when non-empty, like EmailAccount
    sslmode: str | None = None


def _to_dict(row: ExternalDbConnection) -> dict:
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
        "read_only": bool(row.read_only),
    }


def setup_external_db_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/db-connections", tags=["db-connections"],
        dependencies=[Depends(require_admin)],
    )

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
        if not data.host.strip() or not data.database.strip() or not data.username.strip():
            return {"ok": False, "error": "host, database, and username are required"}
        db = SessionLocal()
        try:
            row = ExternalDbConnection(
                id=uuid.uuid4().hex,
                label=label,
                kind="postgres",
                host=data.host.strip(),
                port=int(data.port or 5432),
                database=data.database.strip(),
                username=data.username.strip(),
                password=data.password or "",
                sslmode=(data.sslmode or "prefer").strip(),
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
            for key in ("label", "host", "database", "username", "sslmode"):
                val = getattr(data, key)
                if val is not None:
                    setattr(row, key, val.strip())
            if data.port is not None:
                row.port = int(data.port)
            if data.password:
                row.password = data.password
            db.commit()
            invalidate_schema_cache(conn_id)  # host/db may have changed under this id
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
