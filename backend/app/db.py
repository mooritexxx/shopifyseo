import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator

from shopifyseo.dashboard_config import apply_runtime_settings
from shopifyseo.dashboard_store import DB_PATH, ensure_dashboard_schema
from shopifyseo.db import get_connection, set_journal_mode, set_synchronous


# Schema migration and settings mirroring are idempotent but cost ~15 ms of DDL
# (60+ CREATE IF NOT EXISTS / table_columns probes) per call, which
# dominated short requests when run on every connection. Do it once per DB path;
# an unseen path (tests, a relocated DB) still migrates on first use.
_bootstrapped_paths: set[str] = set()
_bootstrap_lock = threading.Lock()


BUSY_TIMEOUT_MS = 30000  # 30 seconds wait on lock contention (box hotpatch 2026-09-29)


def get_db_path() -> str:
    """Resolve the DB path, guaranteeing the schema exists.

    Callers that hand the bare path to a helper which opens its own connection
    (e.g. ``publish_article``) rely on this having migrated the file.
    """
    if DB_PATH not in _bootstrapped_paths:
        conn = get_connection(
            path=DB_PATH,
            timeout=10,
            row_factory=False,
            wal_mode=False,
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            text_factory=True,
            create_parents=False,
        )
        try:
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
        set_journal_mode(conn, "WAL")
        ensure_dashboard_schema(conn)
        from backend.app.services.team_tasks import ensure_schema as ensure_team_task_schema
        ensure_team_task_schema(conn)
        apply_runtime_settings(conn)
        _bootstrapped_paths.add(path)


def open_db_connection():
    path = DB_PATH
    conn = get_connection(
        path=path,
        timeout=10,
        row_factory=True,
        wal_mode=False,
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        text_factory=True,
        create_parents=False,
    )
    set_synchronous(conn, "NORMAL")
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
