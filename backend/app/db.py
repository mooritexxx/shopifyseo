import sqlite3
from contextlib import contextmanager
from typing import Generator

from shopifyseo.dashboard_store import DB_PATH, bootstrap_runtime_settings, ensure_dashboard_schema
from shopifyseo.sqlite_utf8 import configure_sqlite_text_decode


def get_db_path() -> str:
    bootstrap_runtime_settings()
    return DB_PATH


def open_db_connection():
    conn = sqlite3.connect(get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    configure_sqlite_text_decode(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    ensure_dashboard_schema(conn)
    return conn


@contextmanager
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Open a DB connection and guarantee it is closed on exit."""
    conn = open_db_connection()
    try:
        yield conn
    finally:
        conn.close()
