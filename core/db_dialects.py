"""
core/db_dialects.py

Per-backend detail for Ithaca's external database connections
(core/external_db.py, ExternalDbConnection in core/database.py): URL
construction, read-only session setup, timeout enforcement, and schema
introspection. Kept separate from core/external_db.py because that module's
~200 lines are dialect-independent *policy* (statement shape check, engine
caching, row limits, error mapping) — three dialects' worth of per-backend
detail would drown it.

`get_adapter(kind)` is the only dispatch point. Everything else in this
module is per-dialect implementation behind that one seam, so adding a fourth
SQL dialect (or, later, a non-SQL source like ChromaDB with its own execution
path) never touches core/external_db.py.

Two setup hooks exist, not one, because "read-only" and "timeout" are scoped
differently per dialect:
  - `on_connect(dbapi_conn)` — session-scoped, fired once per new DBAPI
    connection via `sqlalchemy.event.listen(engine, "connect", ...)`. This is
    where SQLite's PRAGMA and MySQL's SET SESSION statements live, since both
    are connection-scoped, not transaction-scoped.
  - `begin_readonly(db_conn)` — transaction-scoped, fired inside
    `db_conn.begin()` for every query. Postgres's `SET LOCAL
    statement_timeout` / `SET TRANSACTION READ ONLY` must be issued per
    transaction, and MySQL's `START TRANSACTION READ ONLY` cannot be issued
    once SQLAlchemy has already opened the transaction — so MySQL's
    read-only enforcement lives in `on_connect` instead.

Introspection deliberately does NOT go through core/external_db.py's
`run_readonly_query` — that would subject trusted, first-party introspection
SQL (e.g. SQLite's `PRAGMA table_info`) to the SELECT-only shape check meant
for user/AI-authored queries.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Optional
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

SUPPORTED_KINDS = ("postgres", "sqlite", "mysql")

STATEMENT_TIMEOUT_MS = 5000


class UnsupportedDialectError(Exception):
    """Raised by get_adapter() for an unknown/unsupported `kind`."""


class DialectAdapter(ABC):
    """One instance per supported `ExternalDbConnection.kind`. Stateless —
    every method takes whatever it needs as an argument."""

    kind: str
    default_port: Optional[int]
    uses_network: bool          # False for sqlite -> connection UI/validation
    uses_sslmode: bool          # True only for postgres
    extra_write_keywords: tuple[str, ...] = ()

    def require_driver(self) -> None:
        """Raise ExternalDbError (via the caller) if the DBAPI driver isn't
        installed. No-op for dialects with no extra dependency."""
        return None

    @abstractmethod
    def build_url(self, conn: Any) -> str:
        ...

    def engine_kwargs(self) -> dict[str, Any]:
        """Overrides merged over core/external_db.py's shared engine defaults."""
        return {}

    def on_connect(self, dbapi_conn: Any) -> None:
        """Session-scoped setup on every new DBAPI connection. No-op default."""
        return None

    def begin_readonly(self, db_conn: Connection) -> None:
        """Transaction-scoped setup, called inside db_conn.begin(). No-op default."""
        return None

    @abstractmethod
    def introspect(self, engine: Engine, row_limit: int) -> list[dict[str, Any]]:
        """-> [{"schema": str, "table": str, "columns": [{"name","type"}]}]"""
        ...

    def probe_sql(self) -> str:
        return "SELECT 1"


class _PostgresAdapter(DialectAdapter):
    kind = "postgres"
    default_port = 5432
    uses_network = True
    uses_sslmode = True

    def build_url(self, conn: Any) -> str:
        user = quote_plus(conn.username or "")
        pwd = quote_plus(conn.password or "")
        return (
            f"postgresql+psycopg://{user}:{pwd}@{conn.host}:{conn.port}"
            f"/{conn.database}?sslmode={conn.sslmode or 'prefer'}"
        )

    def begin_readonly(self, db_conn: Connection) -> None:
        # Order matters: statement_timeout first (today's behavior), then
        # the READ ONLY switch — preserved exactly for the byte-identical
        # Postgres path this refactor requires.
        db_conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
        db_conn.execute(text("SET TRANSACTION READ ONLY"))

    def introspect(self, engine: Engine, row_limit: int) -> list[dict[str, Any]]:
        with engine.connect() as db_conn:
            result = db_conn.execute(text(
                """
                SELECT table_schema, table_name, column_name, data_type, ordinal_position
                FROM information_schema.columns
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name, ordinal_position
                """
            ))
            rows = result.fetchmany(row_limit)
        tables: dict[str, dict[str, Any]] = {}
        for schema, table, column, dtype, _pos in rows:
            key = f"{schema}.{table}"
            entry = tables.setdefault(key, {"schema": schema, "table": table, "columns": []})
            entry["columns"].append({"name": column, "type": dtype})
        return list(tables.values())


class SqlitePathError(Exception):
    """Raised for a SQLite connection whose file_path fails validation."""


def _validate_sqlite_path(path: str) -> str:
    """Confine SQLite connections to real, absolute, non-app-owned files.

    This is the one path-shaped restriction kept despite the "no new network
    restrictions" decision for this feature: a SQLite connection is a
    filesystem read, a genuinely different primitive than reaching a LAN/
    Tailscale host. Without this check a tile could be pointed at the app's
    own SQLite DB and read every EncryptedText ciphertext (API keys, DB
    passwords, session tokens) through a view path that bypasses
    require_admin on the connection-management side.
    """
    if not path or not os.path.isabs(path):
        raise SqlitePathError("file_path must be an absolute path")
    if ".." in path.split(os.sep):
        raise SqlitePathError("file_path must not contain '..' components")
    real = os.path.realpath(path)

    allowed_roots = os.environ.get("ITHACA_SQLITE_ALLOWED_ROOTS", "")
    if allowed_roots:
        roots = [os.path.realpath(r) for r in allowed_roots.split(":") if r.strip()]
        if not any(real == r or real.startswith(r + os.sep) for r in roots):
            raise SqlitePathError("file_path is outside the allowed SQLite roots")

    try:
        from core.database import DATABASE_URL
        own_db_path = DATABASE_URL.replace("sqlite:///", "")
        if own_db_path and not own_db_path.startswith(":"):
            own_real = os.path.realpath(own_db_path)
            if real == own_real:
                raise SqlitePathError("file_path must not point at this app's own database")
    except ImportError:
        pass

    return real


class _SqliteAdapter(DialectAdapter):
    kind = "sqlite"
    default_port = None
    uses_network = False
    uses_sslmode = False
    extra_write_keywords = ("attach", "detach", "pragma", "replace")

    def build_url(self, conn: Any) -> str:
        path = _validate_sqlite_path(conn.file_path or "")
        if not os.path.exists(path):
            raise FileNotFoundError(f"SQLite file not found: {path}")
        # mode=ro opens the file genuinely read-only at the SQLite level —
        # the strongest guarantee available, on top of (not instead of) the
        # PRAGMA query_only session setup below.
        return f"sqlite:///file:{path}?mode=ro&uri=true"

    def engine_kwargs(self) -> dict[str, Any]:
        # pool_pre_ping / pool_recycle are meaningless for a local file and
        # SQLAlchemy's sqlite dialect doesn't use a real connection pool by
        # default; connect_args carries the file: URI mode through.
        return {
            "pool_pre_ping": False,
            "pool_recycle": -1,
            "connect_args": {"uri": True, "timeout": 5},
        }

    def on_connect(self, dbapi_conn: Any) -> None:
        dbapi_conn.execute("PRAGMA query_only = ON")

    def introspect(self, engine: Engine, row_limit: int) -> list[dict[str, Any]]:
        tables: dict[str, dict[str, Any]] = {}
        with engine.connect() as db_conn:
            names = db_conn.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )).fetchmany(row_limit)
            for (name,) in names:
                entry = {"schema": "", "table": name, "columns": []}
                cols = db_conn.execute(text(f'PRAGMA table_info("{name}")')).fetchall()
                for col in cols:
                    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
                    entry["columns"].append({"name": col[1], "type": col[2] or "TEXT"})
                tables[name] = entry
        return list(tables.values())


class _MysqlAdapter(DialectAdapter):
    kind = "mysql"
    default_port = 3306
    uses_network = True
    uses_sslmode = False
    extra_write_keywords = ("replace",)

    def require_driver(self) -> None:
        from src.mysql_runtime import load_pymysql
        load_pymysql()

    def build_url(self, conn: Any) -> str:
        user = quote_plus(conn.username or "")
        pwd = quote_plus(conn.password or "")
        return f"mysql+pymysql://{user}:{pwd}@{conn.host}:{conn.port}/{conn.database}"

    def engine_kwargs(self) -> dict[str, Any]:
        return {"connect_args": {"connect_timeout": 5}}

    def on_connect(self, dbapi_conn: Any) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        except Exception as exc:
            logger.debug("MySQL SET SESSION TRANSACTION READ ONLY failed: %s", exc)
        try:
            # SELECT-only server-side kill switch, MySQL >= 5.7. MariaDB uses
            # max_statement_time (seconds, different variable) and will
            # reject this — degrade silently rather than break the
            # connection over a missing timeout knob.
            cursor.execute(f"SET SESSION max_execution_time = {STATEMENT_TIMEOUT_MS}")
        except Exception as exc:
            logger.debug("MySQL SET SESSION max_execution_time failed: %s", exc)
        finally:
            cursor.close()

    def introspect(self, engine: Engine, row_limit: int) -> list[dict[str, Any]]:
        with engine.connect() as db_conn:
            result = db_conn.execute(text(
                """
                SELECT table_schema, table_name, column_name, data_type, ordinal_position
                FROM information_schema.columns
                WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
                ORDER BY table_schema, table_name, ordinal_position
                """
            ))
            rows = result.fetchmany(row_limit)
        tables: dict[str, dict[str, Any]] = {}
        for schema, table, column, dtype, _pos in rows:
            key = f"{schema}.{table}"
            entry = tables.setdefault(key, {"schema": schema, "table": table, "columns": []})
            entry["columns"].append({"name": column, "type": dtype})
        return list(tables.values())


_ADAPTERS: dict[str, DialectAdapter] = {
    "postgres": _PostgresAdapter(),
    "sqlite": _SqliteAdapter(),
    "mysql": _MysqlAdapter(),
}

_ALIASES = {"postgresql": "postgres", "pg": "postgres"}


def get_adapter(kind: Optional[str]) -> DialectAdapter:
    normalized = _ALIASES.get((kind or "").strip().lower(), (kind or "").strip().lower())
    if not normalized:
        # Every row created before this migration has an explicit kind, but
        # treat empty/None defensively as postgres rather than raising —
        # matches the backfill in core.database's
        # _migrate_add_external_db_file_path.
        logger.warning("ExternalDbConnection with empty kind — defaulting to postgres")
        normalized = "postgres"
    adapter = _ADAPTERS.get(normalized)
    if adapter is None:
        raise UnsupportedDialectError(
            f"Unsupported database kind '{kind}' — expected one of {SUPPORTED_KINDS}"
        )
    return adapter
