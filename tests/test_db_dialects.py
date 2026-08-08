"""Unit tests for core/db_dialects.py — pure logic, no live database servers.

See tests/test_external_db_sqlite.py for the real end-to-end coverage
(engine + read-only enforcement + introspection against an actual SQLite
file), which is the high-value counterpart to these unit checks.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.db_dialects import (
    get_adapter,
    UnsupportedDialectError,
    SUPPORTED_KINDS,
    _validate_sqlite_path,
    SqlitePathError,
)
from core.external_db import _assert_select_only, ExternalDbError


def _conn(**kwargs):
    defaults = dict(id="c1", username="", password="", host="", port=0, database="", file_path="", sslmode="prefer")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ─── get_adapter ─────────────────────────────────────────────────────────


def test_get_adapter_returns_expected_kinds():
    for kind in SUPPORTED_KINDS:
        adapter = get_adapter(kind)
        assert adapter.kind == kind


def test_get_adapter_normalizes_postgresql_alias():
    assert get_adapter("postgresql").kind == "postgres"
    assert get_adapter("PG").kind == "postgres"


def test_get_adapter_unknown_kind_raises():
    with pytest.raises(UnsupportedDialectError):
        get_adapter("oracle")


def test_get_adapter_empty_or_none_defaults_to_postgres():
    assert get_adapter("").kind == "postgres"
    assert get_adapter(None).kind == "postgres"


# ─── build_url — credential quoting ─────────────────────────────────────


def test_postgres_build_url_quotes_special_characters():
    conn = _conn(username="u@x", password="p:w/z", host="10.0.0.5", port=5432, database="d")
    url = get_adapter("postgres").build_url(conn)
    assert "u%40x" in url
    assert "p%3Aw%2Fz" in url
    assert url.startswith("postgresql+psycopg://")


def test_mysql_build_url_quotes_special_characters():
    conn = _conn(username="u@x", password="p:w/z", host="10.0.0.5", port=3306, database="d")
    url = get_adapter("mysql").build_url(conn)
    assert "u%40x" in url
    assert "p%3Aw%2Fz" in url
    assert url.startswith("mysql+pymysql://")


def test_postgres_build_url_handles_unicode_password():
    conn = _conn(username="ünïcode", password="pässwörd", host="h", port=5432, database="d")
    url = get_adapter("postgres").build_url(conn)
    assert "@h:5432/d" in url


# ─── dialect matrix ──────────────────────────────────────────────────────


def test_dialect_matrix():
    pg = get_adapter("postgres")
    assert pg.default_port == 5432 and pg.uses_network is True and pg.uses_sslmode is True

    sq = get_adapter("sqlite")
    assert sq.default_port is None and sq.uses_network is False and sq.uses_sslmode is False

    my = get_adapter("mysql")
    assert my.default_port == 3306 and my.uses_network is True and my.uses_sslmode is False


# ─── _assert_select_only with adapter-specific extra keywords ───────────


def test_assert_select_only_allows_plain_select():
    _assert_select_only("SELECT * FROM widgets")
    _assert_select_only("WITH x AS (SELECT 1) SELECT * FROM x")


def test_assert_select_only_rejects_multiple_statements():
    with pytest.raises(ExternalDbError):
        _assert_select_only("SELECT 1; DROP TABLE widgets")


def test_assert_select_only_rejects_sqlite_attach():
    adapter = get_adapter("sqlite")
    with pytest.raises(ExternalDbError):
        _assert_select_only("ATTACH DATABASE '/etc/passwd' AS x", adapter)


def test_assert_select_only_rejects_pragma_for_sqlite():
    adapter = get_adapter("sqlite")
    with pytest.raises(ExternalDbError):
        _assert_select_only("PRAGMA writable_schema=1", adapter)


def test_assert_select_only_rejects_replace_for_mysql():
    adapter = get_adapter("mysql")
    with pytest.raises(ExternalDbError):
        _assert_select_only("REPLACE INTO widgets VALUES (1)", adapter)


def test_assert_select_only_replace_allowed_for_postgres_without_adapter():
    # No adapter passed -> only the shared _WRITE_KEYWORDS regex applies,
    # which (pre-existing gap, documented in the plan) does not include
    # REPLACE. Confirms the base regex behavior is unchanged by this work.
    _assert_select_only("SELECT replace(name, 'a', 'b') FROM widgets")


# ─── SQLite path validation ──────────────────────────────────────────────


def test_sqlite_path_rejects_relative():
    with pytest.raises(SqlitePathError):
        _validate_sqlite_path("relative/path.db")


def test_sqlite_path_rejects_dotdot(tmp_path):
    p = tmp_path / "a" / ".." / "b.db"
    with pytest.raises(SqlitePathError):
        _validate_sqlite_path(str(p))


def test_sqlite_path_rejects_empty():
    with pytest.raises(SqlitePathError):
        _validate_sqlite_path("")


def test_sqlite_path_rejects_own_app_db(monkeypatch):
    import core.database as coredb
    monkeypatch.setattr(coredb, "DATABASE_URL", "sqlite:////fake/app/data.db")
    with pytest.raises(SqlitePathError):
        _validate_sqlite_path("/fake/app/data.db")


def test_sqlite_path_allows_absolute_outside_app(tmp_path):
    p = tmp_path / "external.db"
    p.write_text("")
    real = _validate_sqlite_path(str(p))
    assert real == os.path.realpath(str(p))


# ─── MySQL driver / session setup ─────────────────────────────────────────


def test_mysql_on_connect_issues_expected_statements():
    adapter = get_adapter("mysql")
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    adapter.on_connect(mock_conn)
    calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert "SET SESSION TRANSACTION READ ONLY" in calls
    assert any("max_execution_time" in c for c in calls)
    mock_cursor.close.assert_called_once()


def test_mysql_on_connect_tolerates_execute_failure():
    adapter = get_adapter("mysql")
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = Exception("MariaDB doesn't know this variable")
    mock_conn.cursor.return_value = mock_cursor
    adapter.on_connect(mock_conn)  # must not raise
    mock_cursor.close.assert_called_once()


def test_mysql_require_driver_missing_raises_runtime_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pymysql", None)  # force ImportError on `import pymysql`
    from core.db_dialects import get_adapter as _get_adapter
    adapter = _get_adapter("mysql")
    with pytest.raises(RuntimeError, match="PyMySQL"):
        adapter.require_driver()
