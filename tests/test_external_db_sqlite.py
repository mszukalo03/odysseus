"""End-to-end tests for SQLite external DB connections — the high-value
counterpart to test_db_dialects.py's unit tests, since SQLite needs zero
external infrastructure (unlike MySQL) to exercise the real engine, the
actual read-only enforcement, and real introspection SQL."""

import sqlite3

import pytest
from sqlalchemy import text

from core.database import SessionLocal, ExternalDbConnection
from core.external_db import (
    run_readonly_query,
    # Aliased: a bare `test_connection` import would be collected by pytest
    # as a test function (any module-level `test_*` callable), which then
    # fails since it isn't one.
    test_connection as _probe_connection,
    _introspect_schema_sync,
    invalidate_schema_cache,
    get_connection,
    _get_engine,
    ExternalDbError,
)
from core.db_dialects import get_adapter


@pytest.fixture
def sqlite_fixture(tmp_path):
    """A small on-disk SQLite DB with a table and a view, registered as an
    ExternalDbConnection row in the app's own (in-memory, per conftest) DB."""
    db_path = tmp_path / "fixture.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT, price REAL)")
    conn.executemany(
        "INSERT INTO widgets (name, price) VALUES (?, ?)",
        [(f"widget-{i}", float(i)) for i in range(5)],
    )
    conn.execute("CREATE VIEW cheap_widgets AS SELECT * FROM widgets WHERE price < 2")
    conn.commit()
    conn.close()

    conn_id = f"test-sqlite-{tmp_path.name}"
    db = SessionLocal()
    try:
        row = ExternalDbConnection(
            id=conn_id, label="fixture", kind="sqlite",
            host="", port=0, database="", username="", sslmode="",
            file_path=str(db_path),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    yield conn_id, str(db_path)

    invalidate_schema_cache(conn_id)
    db = SessionLocal()
    try:
        row = db.get(ExternalDbConnection, conn_id)
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def test_run_readonly_query_returns_rows(sqlite_fixture):
    conn_id, _ = sqlite_fixture
    result = run_readonly_query(conn_id, "SELECT id, name, price FROM widgets ORDER BY id")
    assert result["columns"] == ["id", "name", "price"]
    assert len(result["rows"]) == 5
    assert result["rows"][0] == [1, "widget-0", 0.0]
    assert result["truncated"] is False


def test_run_readonly_query_truncates_past_row_limit(sqlite_fixture):
    conn_id, _ = sqlite_fixture
    result = run_readonly_query(conn_id, "SELECT * FROM widgets", row_limit=2)
    assert len(result["rows"]) == 2
    assert result["truncated"] is True


def test_write_is_blocked_at_the_db_layer(sqlite_fixture):
    """Bypass the regex shape-check entirely (call the engine directly) to
    prove the *database* itself rejects writes — PRAGMA query_only plus the
    mode=ro URI, not just _assert_select_only."""
    conn_id, _ = sqlite_fixture
    conn = get_connection(conn_id)
    adapter = get_adapter(conn.kind)
    engine = _get_engine(conn, adapter)
    with pytest.raises(Exception, match="readonly"):
        with engine.connect() as db_conn:
            db_conn.execute(text("INSERT INTO widgets (name, price) VALUES ('x', 9)"))


def test_introspect_schema_lists_table_and_view(sqlite_fixture):
    conn_id, _ = sqlite_fixture
    # Calls the sync introspection directly rather than the async
    # `introspect_schema` wrapper: that wrapper runs via asyncio.to_thread,
    # and under this test suite's sqlite:///:memory: app DB (per
    # tests/conftest.py) SQLAlchemy's SingletonThreadPool hands a *different*
    # thread a fresh, empty in-memory database — a test-harness limitation,
    # not something specific to this feature (introspect_schema has always
    # gone through to_thread). Real deployments use a file-backed app DB,
    # where this isn't an issue.
    tables = _introspect_schema_sync(conn_id)
    names = {t["table"] for t in tables}
    assert "widgets" in names
    assert "cheap_widgets" in names
    assert not any(n.startswith("sqlite_") for n in names)
    widgets = next(t for t in tables if t["table"] == "widgets")
    assert widgets["schema"] == ""
    col_names = {c["name"] for c in widgets["columns"]}
    assert col_names == {"id", "name", "price"}


def test_connection_ok(sqlite_fixture):
    conn_id, _ = sqlite_fixture
    result = _probe_connection(conn_id)
    assert result == {"ok": True, "error": None}


def test_connection_missing_file_fails_without_creating_it(tmp_path):
    missing_path = tmp_path / "does-not-exist.db"
    conn_id = "test-sqlite-missing"
    db = SessionLocal()
    try:
        row = ExternalDbConnection(
            id=conn_id, label="missing", kind="sqlite",
            host="", port=0, database="", username="", sslmode="",
            file_path=str(missing_path),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()
    try:
        result = _probe_connection(conn_id)
        assert result["ok"] is False
        assert not missing_path.exists()
    finally:
        db = SessionLocal()
        db.query(ExternalDbConnection).filter_by(id=conn_id).delete()
        db.commit()
        db.close()


def test_tile_round_trip_table_viz(sqlite_fixture):
    conn_id, _ = sqlite_fixture
    from extensions.ithaca.tile_schema import TileConfig
    from extensions.ithaca.tiles import _run_tile_sync

    cfg = TileConfig.model_validate({
        "schema_version": 1,
        "id": "sqlite_widgets",
        "title": "Widgets",
        "data_source": {"type": "sqlite", "connection_ref": conn_id, "query": "SELECT id, name, price FROM widgets ORDER BY id"},
        "viz": {"type": "table"},
    })
    shaped = _run_tile_sync(cfg)
    assert shaped["type"] == "table"
    assert shaped["columns"] == ["id", "name", "price"]
    assert len(shaped["rows"]) == 5
    assert shaped["rows"][0] == {"id": 1, "name": "widget-0", "price": 0.0}


def test_tile_round_trip_stat_viz(sqlite_fixture):
    conn_id, _ = sqlite_fixture
    from extensions.ithaca.tile_schema import TileConfig
    from extensions.ithaca.tiles import _run_tile_sync

    cfg = TileConfig.model_validate({
        "schema_version": 1,
        "id": "sqlite_widget_count",
        "title": "Widget Count",
        "data_source": {"type": "sqlite", "connection_ref": conn_id, "query": "SELECT 'count' AS label, count(*) AS n FROM widgets"},
        "viz": {"type": "stat", "value_field": "n"},
    })
    shaped = _run_tile_sync(cfg)
    assert shaped == {"type": "stat", "value": 5.0}


def test_tile_tolerates_kind_mismatch(sqlite_fixture, caplog):
    """data_source.type says postgres but the bound connection is sqlite —
    should still run, just logging a note (core/db_dialects.py's `kind` on
    the connection row is authoritative, not the tile's authoring hint)."""
    conn_id, _ = sqlite_fixture
    from extensions.ithaca.tile_schema import TileConfig
    from extensions.ithaca.tiles import _run_tile_sync

    cfg = TileConfig.model_validate({
        "schema_version": 1,
        "id": "mismatched",
        "title": "Mismatched",
        "data_source": {"type": "postgres", "connection_ref": conn_id, "query": "SELECT count(*) AS n FROM widgets"},
        "viz": {"type": "table"},
    })
    shaped = _run_tile_sync(cfg)
    assert shaped["rows"] == [{"n": 5}]
