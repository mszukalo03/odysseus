"""Tests for routes/external_db_routes.py's kind-aware CRUD validation —
create/update per kind, kind immutability, the /kinds capability endpoint,
and _to_dict's new fields. Endpoint coroutines are called directly (bypassing
the require_admin FastAPI dependency, which is auth plumbing already covered
elsewhere) to unit-test the validation logic itself."""

from fastapi import Request

from core.database import SessionLocal, ExternalDbConnection
import routes.external_db_routes as edr


def _req():
    return Request(scope={"type": "http", "headers": []})


_PREFIX = "/api/db-connections"


def _route(router, path, method):
    full = _PREFIX + path
    for r in router.routes:
        if r.path == full and method.upper() in r.methods:
            return r.endpoint
    raise AssertionError(f"no route for {method} {full}")


def _cleanup(conn_id):
    db = SessionLocal()
    try:
        db.query(ExternalDbConnection).filter_by(id=conn_id).delete()
        db.commit()
    finally:
        db.close()


async def _create(router, **fields):
    endpoint = _route(router, "", "POST")
    data = edr.DbConnectionCreate(**fields)
    return await endpoint(data, _req())


async def _update(router, conn_id, **fields):
    endpoint = _route(router, "/{conn_id}", "PUT")
    data = edr.DbConnectionUpdate(**fields)
    return await endpoint(conn_id, data, _req())


async def _list(router):
    endpoint = _route(router, "", "GET")
    return await endpoint()


async def _kinds(router):
    endpoint = _route(router, "/kinds", "GET")
    return await endpoint()


def test_kinds_endpoint_lists_supported_kinds(anyio_backend=None):
    import asyncio
    router = edr.setup_external_db_routes()
    result = asyncio.run(_kinds(router))
    kinds = {k["kind"]: k for k in result["kinds"]}
    assert set(kinds) == {"postgres", "sqlite", "mysql"}
    assert kinds["postgres"]["default_port"] == 5432
    assert kinds["sqlite"]["default_port"] is None
    assert "file_path" in kinds["sqlite"]["fields"]
    assert "host" not in kinds["sqlite"]["fields"]
    assert kinds["mysql"]["default_port"] == 3306


def test_create_sqlite_without_host_succeeds():
    import asyncio
    router = edr.setup_external_db_routes()
    result = asyncio.run(_create(
        router, label="sqlite-test", kind="sqlite", file_path="/tmp/whatever.db",
    ))
    assert result["ok"] is True
    try:
        db = SessionLocal()
        row = db.get(ExternalDbConnection, result["id"])
        assert row.kind == "sqlite"
        assert row.file_path == "/tmp/whatever.db"
        assert row.host == ""
        db.close()
    finally:
        _cleanup(result["id"])


def test_create_sqlite_without_file_path_fails():
    import asyncio
    router = edr.setup_external_db_routes()
    result = asyncio.run(_create(router, label="sqlite-bad", kind="sqlite"))
    assert result["ok"] is False
    assert "file_path" in result["error"]


def test_create_postgres_without_host_fails():
    import asyncio
    router = edr.setup_external_db_routes()
    result = asyncio.run(_create(router, label="pg-bad", kind="postgres", database="d", username="u"))
    assert result["ok"] is False


def test_create_unknown_kind_fails():
    import asyncio
    router = edr.setup_external_db_routes()
    result = asyncio.run(_create(router, label="bad-kind", kind="oracle"))
    assert result["ok"] is False
    assert "kind" in result["error"]


def test_create_mysql_defaults_port():
    import asyncio
    router = edr.setup_external_db_routes()
    result = asyncio.run(_create(
        router, label="mysql-test", kind="mysql", host="h", database="d", username="u",
    ))
    assert result["ok"] is True
    try:
        db = SessionLocal()
        row = db.get(ExternalDbConnection, result["id"])
        assert row.port == 3306
        db.close()
    finally:
        _cleanup(result["id"])


def test_update_cannot_change_kind():
    """DbConnectionUpdate has no `kind` field at all — changing kind after
    create is unsupported by design (see routes/external_db_routes.py's
    docstring on DbConnectionUpdate)."""
    assert not hasattr(edr.DbConnectionUpdate(), "kind")


def test_update_sqlite_file_path():
    import asyncio
    router = edr.setup_external_db_routes()
    created = asyncio.run(_create(router, label="sqlite-upd", kind="sqlite", file_path="/tmp/a.db"))
    try:
        result = asyncio.run(_update(router, created["id"], file_path="/tmp/b.db"))
        assert result["ok"] is True
        db = SessionLocal()
        row = db.get(ExternalDbConnection, created["id"])
        assert row.file_path == "/tmp/b.db"
        db.close()
    finally:
        _cleanup(created["id"])


def test_to_dict_includes_new_fields():
    db = SessionLocal()
    row = ExternalDbConnection(
        id="to-dict-test", label="x", kind="sqlite",
        host="", port=0, database="", username="", sslmode="", file_path="/tmp/x.db",
    )
    db.add(row)
    db.commit()
    try:
        d = edr._to_dict(row)
        assert d["file_path"] == "/tmp/x.db"
        assert d["kind"] == "sqlite"
        assert "driver_available" in d
        assert "driver_hint" in d
    finally:
        db.close()
        _cleanup("to-dict-test")


def test_list_connections_returns_new_connection():
    import asyncio
    router = edr.setup_external_db_routes()
    created = asyncio.run(_create(router, label="list-test", kind="sqlite", file_path="/tmp/list.db"))
    try:
        result = asyncio.run(_list(router))
        ids = {c["id"] for c in result["connections"]}
        assert created["id"] in ids
    finally:
        _cleanup(created["id"])
