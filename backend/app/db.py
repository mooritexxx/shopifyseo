import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator

from shopifyseo.dashboard_config import apply_runtime_settings
from shopifyseo.dashboard_store import DB_PATH, ensure_dashboard_schema
from shopifyseo.sqlite_utf8 import configure_sqlite_text_decode


# Schema migration and settings mirroring are idempotent but cost ~15 ms of DDL
# (60+ CREATE IF NOT EXISTS / PRAGMA table_info statements) per call, which
# dominated short requests when run on every connection. Do it once per DB path;
# an unseen path (tests, a relocated DB) still migrates on first use.
_bootstrapped_paths: set[str] = set()
_bootstrap_lock = threading.Lock()


def get_db_path() -> str:
    """Resolve the DB path, guaranteeing the schema exists.

    Callers that hand the bare path to a helper which opens its own connection
    (e.g. ``publish_article``) rely on this having migrated the file.
    """
    if DB_PATH not in _bootstrapped_paths:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            configure_sqlite_text_decode(conn)
            _bootstrap_once(conn, DB_PATH)
        finally:
            conn.close()
    return DB_PATH


def _bootstrap_once(conn: sqlite3.Connection, path: str) -> None:
    """Apply schema + runtime settings the first time a DB path is opened.

    Settings changes made after bootstrap still propagate: ``settings_service``
    and the OAuth callbacks call ``apply_runtime_settings`` themselves on save.
    """
    if path in _bootstrapped_paths:
        return
    with _bootstrap_lock:
        if path in _bootstrapped_paths:
            return
        conn.execute("PRAGMA journal_mode = WAL")
        ensure_dashboard_schema(conn)
        apply_runtime_settings(conn)
        _bootstrapped_paths.add(path)


def open_db_connection():
    path = DB_PATH
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    configure_sqlite_text_decode(conn)
    conn.execute("PRAGMA synchronous = NORMAL")
    _bootstrap_once(conn, path)
    return conn


@contextmanager
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Open a DB connection and guarantee it is closed on exit."""
    conn = open_db_connection()
    try:
        yield conn
    finally:
        conn.close()
