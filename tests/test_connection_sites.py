"""Per-site SQLite settings after routing sqlite3.connect through get_connection().

Expected values are hard-coded from main (3a0b4c4) before this PR:
each site's sqlite3.connect() kwargs plus every PRAGMA that site ran.
"""
from __future__ import annotations

import ast
import io
import os
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from shopifyseo.sqlite_utf8 import utf8_text_factory


REPO_ROOT = Path(__file__).resolve().parent.parent

# Hard-coded from main before this PR. timeout is not a Connection attribute
# (Python 3.12); it is asserted via the get_connection kwargs the site passes.
# journal_mode / synchronous / busy_timeout / foreign_keys are PRAGMA integers
# or names as sqlite3 returns them on a file DB.

# backend/app/db.py get_db_path: connect(timeout=10), no row_factory,
# PRAGMA busy_timeout=30000, _bootstrap_once PRAGMA journal_mode=WAL,
# no synchronous PRAGMA (FULL=2). ensure_schema sets PRAGMA foreign_keys=ON.
GET_DB_PATH = {
    "row_factory": None,
    "isolation_level": "",
    "journal_mode": "wal",
    "synchronous": 2,
    "busy_timeout": 30000,
    "foreign_keys": 1,
    "text_factory": utf8_text_factory,
    "timeout": 10,
}

# backend/app/db.py open_db_connection: connect(timeout=10), sqlite3.Row,
# PRAGMA busy_timeout=30000, PRAGMA synchronous=NORMAL (1) at the site,
# _bootstrap_once PRAGMA journal_mode=WAL (first call only).
# ensure_schema sets foreign_keys=ON on first call.
OPEN_DB_CONNECTION = {
    "row_factory": sqlite3.Row,
    "isolation_level": "",
    "journal_mode": "wal",
    "synchronous": 1,
    "busy_timeout": 30000,
    "foreign_keys": 1,
    "text_factory": utf8_text_factory,
    "timeout": 10,
}

# shopifyseo/dashboard_store.py db_connect: connect(timeout=10), sqlite3.Row,
# PRAGMA busy_timeout=30000, journal_mode=WAL, synchronous=NORMAL.
# ensure_schema sets foreign_keys=ON.
DB_CONNECT = {
    "row_factory": sqlite3.Row,
    "isolation_level": "",
    "journal_mode": "wal",
    "synchronous": 1,
    "busy_timeout": 30000,
    "foreign_keys": 1,
    "text_factory": utf8_text_factory,
    "timeout": 10,
}

# shopifyseo/dashboard_store.py bootstrap_runtime_settings: connect(timeout=10),
# sqlite3.Row, PRAGMA busy_timeout=30000, no journal_mode (delete), no
# synchronous (FULL=2). ensure_schema sets foreign_keys=ON.
BOOTSTRAP_RUNTIME_SETTINGS = {
    "row_factory": sqlite3.Row,
    "isolation_level": "",
    "journal_mode": "delete",
    "synchronous": 2,
    "busy_timeout": 30000,
    "foreign_keys": 1,
    "text_factory": utf8_text_factory,
    "timeout": 10,
}

# shopifyseo/dashboard_actions/_state.py _db_connect_for_actions:
# connect(timeout=30), sqlite3.Row, PRAGMA busy_timeout=30000,
# journal_mode=WAL, synchronous=NORMAL, no foreign_keys.
DB_CONNECT_FOR_ACTIONS = {
    "row_factory": sqlite3.Row,
    "isolation_level": "",
    "journal_mode": "wal",
    "synchronous": 1,
    "busy_timeout": 30000,
    "foreign_keys": 0,
    "text_factory": utf8_text_factory,
    "timeout": 30,
}

# shopifyseo/shopify_catalog_sync/__init__.py print_summary:
# connect() default timeout=5.0 (observable as busy_timeout=5000), sqlite3.Row,
# no busy_timeout PRAGMA, no journal_mode (delete), no synchronous (FULL=2),
# no foreign_keys.
PRINT_SUMMARY = {
    "row_factory": sqlite3.Row,
    "isolation_level": "",
    "journal_mode": "delete",
    "synchronous": 2,
    "busy_timeout": 5000,
    "foreign_keys": 0,
    "text_factory": utf8_text_factory,
    "timeout": 5.0,
}

# shopifyseo/shopify_catalog_sync/db.py open_db: connect(timeout=30), sqlite3.Row,
# PRAGMA busy_timeout=30000, then after ensure_schema (which sets foreign_keys=ON)
# journal_mode=WAL and synchronous=NORMAL.
OPEN_DB = {
    "row_factory": sqlite3.Row,
    "isolation_level": "",
    "journal_mode": "wal",
    "synchronous": 1,
    "busy_timeout": 30000,
    "foreign_keys": 1,
    "text_factory": utf8_text_factory,
    "timeout": 30,
}


@pytest.fixture
def unset_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def _pragma(conn, name):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def snapshot_settings(conn):
    settings = {
        "row_factory": conn.row_factory,
        "isolation_level": conn.isolation_level,
        "journal_mode": str(_pragma(conn, "journal_mode")).lower(),
        "synchronous": int(_pragma(conn, "synchronous")),
        "busy_timeout": int(_pragma(conn, "busy_timeout")),
        "foreign_keys": int(_pragma(conn, "foreign_keys")),
        "text_factory": conn.text_factory,
    }
    if hasattr(conn, "timeout"):
        settings["timeout"] = conn.timeout
    return settings


def assert_settings(actual, expected):
    for key in (
        "row_factory",
        "isolation_level",
        "journal_mode",
        "synchronous",
        "busy_timeout",
        "foreign_keys",
        "text_factory",
    ):
        assert actual[key] == expected[key], f"{key}: {actual[key]!r} != {expected[key]!r}"
    if "timeout" in actual:
        assert actual["timeout"] == expected["timeout"]


class _CloseSnapshotProxy:
    """Delegate to a live sqlite3 connection and snapshot PRAGMAs on close()."""

    def __init__(self, conn, recorded):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_recorded", recorded)

    def close(self):
        recorded = object.__getattribute__(self, "_recorded")
        conn = object.__getattribute__(self, "_conn")
        if "settings" not in recorded:
            recorded["settings"] = snapshot_settings(conn)
        return conn.close()

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)


def wrap_get_connection(monkeypatch, module, *, snapshot_on_close=False):
    """Record get_connection kwargs; optionally snapshot PRAGMAs when the site closes."""
    recorded = {}
    real = module.get_connection

    def wrapper(**kwargs):
        recorded.clear()
        recorded.update(kwargs)
        conn = real(**kwargs)
        recorded["conn"] = conn
        if snapshot_on_close:
            return _CloseSnapshotProxy(conn, recorded)
        return conn

    monkeypatch.setattr(module, "get_connection", wrapper)
    return recorded


def test_get_db_path_preserves_sqlite_settings(tmp_path, monkeypatch, unset_database_url):
    from backend.app import db as app_db

    db_path = str(tmp_path / "catalog.sqlite3")
    monkeypatch.setattr(app_db, "DB_PATH", db_path)
    app_db._bootstrapped_paths.clear()
    recorded = wrap_get_connection(monkeypatch, app_db, snapshot_on_close=True)
    try:
        try:
            assert app_db.get_db_path() == db_path
        except TypeError as exc:
            # Main did not set row_factory on this site; _table_columns uses
            # row["name"]. WAL / foreign_keys PRAGMAs still run before that.
            assert "tuple indices" in str(exc)
        assert recorded["timeout"] == GET_DB_PATH["timeout"]
        assert_settings(recorded["settings"], GET_DB_PATH)
    finally:
        app_db._bootstrapped_paths.clear()


def test_open_db_connection_preserves_sqlite_settings(tmp_path, monkeypatch, unset_database_url):
    from backend.app import db as app_db

    db_path = str(tmp_path / "catalog.sqlite3")
    monkeypatch.setattr(app_db, "DB_PATH", db_path)
    app_db._bootstrapped_paths.clear()
    recorded = wrap_get_connection(monkeypatch, app_db)
    try:
        conn = app_db.open_db_connection()
        try:
            assert recorded["timeout"] == OPEN_DB_CONNECTION["timeout"]
            assert_settings(snapshot_settings(conn), OPEN_DB_CONNECTION)
        finally:
            conn.close()

        import importlib

        connect_mod = importlib.import_module("shopifyseo.db.connect")
        traced = []
        real_connect = connect_mod.sqlite3.connect

        def traced_connect(*args, **kwargs):
            traced_conn = real_connect(*args, **kwargs)

            def _trace(sql):
                if str(sql).upper().lstrip().startswith("PRAGMA"):
                    traced.append(sql)

            traced_conn.set_trace_callback(_trace)
            return traced_conn

        monkeypatch.setattr(connect_mod.sqlite3, "connect", traced_connect)
        conn2 = app_db.open_db_connection()
        try:
            assert traced == [
                "PRAGMA busy_timeout = 30000",
                "PRAGMA synchronous = NORMAL",
            ]
        finally:
            conn2.close()
    finally:
        app_db._bootstrapped_paths.clear()


def test_dashboard_store_db_connect_preserves_sqlite_settings(
    tmp_path, monkeypatch, unset_database_url
):
    from shopifyseo import dashboard_store

    original = dashboard_store.DB_PATH
    dashboard_store.DB_PATH = str(tmp_path / "catalog.sqlite3")
    recorded = wrap_get_connection(monkeypatch, dashboard_store)
    try:
        conn = dashboard_store.db_connect()
        try:
            assert recorded["timeout"] == DB_CONNECT["timeout"]
            assert_settings(snapshot_settings(conn), DB_CONNECT)
        finally:
            conn.close()
    finally:
        dashboard_store.DB_PATH = original


def test_bootstrap_runtime_settings_preserves_sqlite_settings(
    tmp_path, monkeypatch, unset_database_url
):
    from shopifyseo import dashboard_store

    original = dashboard_store.DB_PATH
    dashboard_store.DB_PATH = str(tmp_path / "catalog.sqlite3")
    recorded = wrap_get_connection(monkeypatch, dashboard_store, snapshot_on_close=True)
    try:
        dashboard_store.bootstrap_runtime_settings()
        assert recorded["timeout"] == BOOTSTRAP_RUNTIME_SETTINGS["timeout"]
        assert_settings(recorded["settings"], BOOTSTRAP_RUNTIME_SETTINGS)
    finally:
        dashboard_store.DB_PATH = original


def test_db_connect_for_actions_preserves_sqlite_settings(
    tmp_path, monkeypatch, unset_database_url
):
    from shopifyseo.dashboard_actions import _state

    recorded = wrap_get_connection(monkeypatch, _state)
    conn = _state._db_connect_for_actions(str(tmp_path / "catalog.sqlite3"))
    try:
        assert recorded["timeout"] == DB_CONNECT_FOR_ACTIONS["timeout"]
        assert_settings(snapshot_settings(conn), DB_CONNECT_FOR_ACTIONS)
    finally:
        conn.close()


def test_print_summary_preserves_sqlite_settings(tmp_path, monkeypatch, unset_database_url):
    import shopifyseo.shopify_catalog_sync as catalog_init

    db_path = tmp_path / "catalog.sqlite3"
    # SQLite-only on purpose: seed a file DB for print_summary PRAGMA/settings assertions.
    setup = sqlite3.connect(db_path)
    try:
        for table in (
            "products",
            "product_variants",
            "product_images",
            "product_metafields",
            "collections",
            "collection_metafields",
            "collection_products",
            "pages",
            "blogs",
            "blog_articles",
        ):
            setup.execute(f"CREATE TABLE {table} (id INTEGER)")
        setup.execute(
            """
            CREATE TABLE sync_runs (
                id INTEGER, started_at TEXT, finished_at TEXT, status TEXT,
                products_synced INTEGER, variants_synced INTEGER, images_synced INTEGER,
                metafields_synced INTEGER, collections_synced INTEGER,
                collection_metafields_synced INTEGER, collection_products_synced INTEGER,
                pages_synced INTEGER, blogs_synced INTEGER, blog_articles_synced INTEGER
            )
            """
        )
        setup.commit()
    finally:
        setup.close()

    recorded = wrap_get_connection(monkeypatch, catalog_init, snapshot_on_close=True)
    with redirect_stdout(io.StringIO()):
        catalog_init.print_summary(db_path)
    assert recorded["timeout"] == PRINT_SUMMARY["timeout"]
    assert_settings(recorded["settings"], PRINT_SUMMARY)


def test_ensure_schema_enables_foreign_keys_when_transaction_open(
    tmp_path, unset_database_url
):
    """SQLite ignores PRAGMA foreign_keys inside an open txn; set it after executescript."""
    from shopifyseo.shopify_catalog_sync.db import ensure_schema

    path = tmp_path / "fk-txn.sqlite3"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE probe (id INTEGER)")
        conn.execute("INSERT INTO probe (id) VALUES (1)")
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        ensure_schema(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_catalog_sync_open_db_preserves_sqlite_settings(
    tmp_path, monkeypatch, unset_database_url
):
    from shopifyseo.shopify_catalog_sync import db as catalog_db

    recorded = wrap_get_connection(monkeypatch, catalog_db)
    conn = catalog_db.open_db(tmp_path / "catalog.sqlite3")
    try:
        assert recorded["timeout"] == OPEN_DB["timeout"]
        assert_settings(snapshot_settings(conn), OPEN_DB)
    finally:
        conn.close()


def test_no_sqlite3_connect_outside_shopifyseo_db():
    """Fail if a new sqlite3.connect appears in app code outside shopifyseo/db/."""
    offenders = []
    for root_name in ("backend", "shopifyseo"):
        root = REPO_ROOT / root_name
        for py_file in root.rglob("*.py"):
            rel = py_file.relative_to(REPO_ROOT)
            if rel.parts[:2] == ("shopifyseo", "db"):
                continue
            source = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(rel))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "connect"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "sqlite3"
                ):
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], "sqlite3.connect remains outside shopifyseo/db/:\n  " + "\n  ".join(
        offenders
    )
