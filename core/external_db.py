"""
core/external_db.py

Read-only query execution against admin-configured *external* Postgres
connections (`ExternalDbConnection` in core/database.py) — databases this app
does not own, e.g. a homelab automation's own Postgres instance used as an
Ithaca dashboard tile's data source. Kept deliberately separate from this
app's own SQLAlchemy engine/session (`core.database.engine`/`SessionLocal`):
mixing connection strings here would be a correctness hazard, not a style
choice — the app's own DB and an admin-added external DB must never share a
session or engine.

This module is generic to *any* Postgres connection/table shape — nothing
here assumes a particular schema. `introspect_schema` reads whatever tables
exist via `information_schema`, and `run_readonly_query` executes whatever
SELECT it's given.

Safety model (defense in depth — a bad hand-written or AI-generated query
must never mutate a database this app doesn't own):
  1. Statement shape check: reject anything but a single SELECT/CTE-SELECT
     statement before it ever reaches Postgres (fast-fail, friendly error).
  2. `SET TRANSACTION READ ONLY` on every session — Postgres itself rejects
     any write regardless of what layer 1 missed.
  3. `statement_timeout` — a slow/runaway query cannot hang the shared DB.
  4. `row_limit` — results are truncated before they reach the caller.

The real safety boundary should still be a dedicated read-only Postgres role
for the connection (documented in the deployment guide) — this module is a
second line of defense, not a substitute for least-privilege credentials.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from core.database import SessionLocal, ExternalDbConnection
from core.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

DEFAULT_ROW_LIMIT = 500
INTROSPECT_ROW_LIMIT = 5000
STATEMENT_TIMEOUT_MS = 5000
# Schema rarely changes and a tile-builder session often introspects the same
# connection repeatedly (Generate, tweak, Generate again) — a short cache
# avoids re-querying information_schema on every call without risking a
# stale schema for long (see also invalidate_schema_cache, called on
# connection update/delete since host/db could change under the same id).
SCHEMA_CACHE_TTL = 60

_engine_cache: dict[str, Engine] = {}
_schema_cache = TTLCache()

_WRITE_KEYWORDS = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|"
    r"vacuum|reindex|copy|call|do|merge|lock)\b"
)


class ExternalDbError(Exception):
    """User-facing error for external DB connection/query failures."""


def _build_url(conn: ExternalDbConnection) -> str:
    user = quote_plus(conn.username or "")
    pwd = quote_plus(conn.password or "")
    return (
        f"postgresql+psycopg://{user}:{pwd}@{conn.host}:{conn.port}"
        f"/{conn.database}?sslmode={conn.sslmode or 'prefer'}"
    )


def _get_engine(conn: ExternalDbConnection) -> Engine:
    """Short-lived, per-connection engine — small pool, pre-ping so a stale
    connection (DB restart, network blip) is dropped rather than reused.
    Cache key includes `updated_at` so an edited connection (new host/creds)
    gets a fresh engine instead of reusing one built from the old URL."""
    cache_key = f"{conn.id}:{conn.updated_at}"
    engine = _engine_cache.get(cache_key)
    if engine is not None:
        return engine
    for stale_key in [k for k in _engine_cache if k.startswith(f"{conn.id}:")]:
        _engine_cache.pop(stale_key).dispose()
    engine = create_engine(
        _build_url(conn),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_recycle=300,
    )
    _engine_cache[cache_key] = engine
    return engine


def get_connection(conn_id: str) -> ExternalDbConnection:
    db = SessionLocal()
    try:
        conn = db.get(ExternalDbConnection, conn_id)
        if not conn:
            raise ExternalDbError(f"No database connection configured with id '{conn_id}'")
        db.expunge(conn)
        return conn
    finally:
        db.close()


def list_connections() -> list[ExternalDbConnection]:
    db = SessionLocal()
    try:
        rows = db.query(ExternalDbConnection).order_by(ExternalDbConnection.label.asc()).all()
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def _assert_select_only(sql: str) -> None:
    """Fast-fail shape check: reject anything but a single SELECT/CTE-SELECT.
    Not a full SQL parser — the real safety net is the READ ONLY transaction
    in `run_readonly_query`; this just gives a friendlier error before the
    query ever reaches the network."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ExternalDbError("Query is empty")
    if ";" in stripped:
        raise ExternalDbError("Multiple statements are not allowed — submit a single SELECT query")
    lowered = stripped.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ExternalDbError("Only SELECT (or WITH ... SELECT) queries are allowed")
    if _WRITE_KEYWORDS.search(stripped):
        raise ExternalDbError("Query contains a disallowed write/DDL keyword")


def run_readonly_query(
    conn_id: str, sql: str, params: Optional[dict] = None, row_limit: int = DEFAULT_ROW_LIMIT
) -> dict[str, Any]:
    """Execute a single read-only SELECT against an external connection.

    Returns {"columns": [...], "rows": [[...], ...], "truncated": bool}.
    """
    _assert_select_only(sql)
    conn = get_connection(conn_id)
    engine = _get_engine(conn)
    try:
        with engine.connect() as db_conn:
            with db_conn.begin():
                db_conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                db_conn.execute(text("SET TRANSACTION READ ONLY"))
                result = db_conn.execute(text(sql), params or {})
                columns = list(result.keys())
                rows = []
                truncated = False
                for i, row in enumerate(result):
                    if i >= row_limit:
                        truncated = True
                        break
                    rows.append(list(row))
                # Always roll back — this is a read path. READ ONLY already
                # blocks writes, but never committing is the belt to that
                # transaction's suspenders on a DB we don't own.
                db_conn.rollback()
        return {"columns": columns, "rows": rows, "truncated": truncated}
    except ExternalDbError:
        raise
    except Exception as exc:
        logger.warning("External query failed on connection %s: %s", conn_id, exc)
        raise ExternalDbError(str(exc)) from exc


def _introspect_schema_sync(conn_id: str) -> list[dict[str, Any]]:
    result = run_readonly_query(
        conn_id,
        """
        SELECT table_schema, table_name, column_name, data_type, ordinal_position
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name, ordinal_position
        """,
        row_limit=INTROSPECT_ROW_LIMIT,
    )
    tables: dict[str, dict[str, Any]] = {}
    for schema, table, column, dtype, _pos in result["rows"]:
        key = f"{schema}.{table}"
        entry = tables.setdefault(key, {"schema": schema, "table": table, "columns": []})
        entry["columns"].append({"name": column, "type": dtype})
    return list(tables.values())


async def introspect_schema(conn_id: str) -> list[dict[str, Any]]:
    """List tables + columns visible to this connection's role, via
    information_schema — generic to any Postgres schema/table shape, used by
    both a manual "pick a table" UI and the AI tile-proposal flow. TTL-cached
    (see core/ttl_cache.py) since a tile-builder session often introspects
    the same connection repeatedly."""
    return await _schema_cache.get(
        conn_id, SCHEMA_CACHE_TTL, lambda: asyncio.to_thread(_introspect_schema_sync, conn_id),
    )


def invalidate_schema_cache(conn_id: str) -> None:
    """Drop a connection's cached schema — call after editing/deleting a
    connection, since its host/database (and therefore schema) may differ
    under the same id."""
    _schema_cache.invalidate(conn_id)


def test_connection(conn_id: str) -> dict[str, Any]:
    """Cheap reachability + read-only sanity check for the Settings UI's
    Test button."""
    try:
        run_readonly_query(conn_id, "SELECT 1", row_limit=1)
        return {"ok": True, "error": None}
    except ExternalDbError as exc:
        return {"ok": False, "error": str(exc)}
