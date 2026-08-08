"""Small helper for the optional MySQL runtime dependency (PyMySQL), used by
core/db_dialects.py's MySQL adapter for Ithaca external DB connections."""

MYSQL_PYMYSQL_MISSING = (
    "MySQL connections require PyMySQL. Install optional dependencies with "
    "`pip install -r requirements-optional.txt`."
)


def load_pymysql():
    """Return the pymysql module, or raise a user-facing setup hint."""
    try:
        import pymysql  # optional MySQL DBAPI driver
    except ImportError as exc:
        raise RuntimeError(MYSQL_PYMYSQL_MISSING) from exc
    return pymysql
