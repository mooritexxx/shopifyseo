"""Dashboard-area type/dialect cleanup (PostgreSQL PR 6a).

Guards the mechanical sqlite3 → shopifyseo.db swap in dashboard_store,
dashboard_queries, and dashboard_actions. Behaviour with DATABASE_URL unset
must match main: live connections still use sqlite3.Row from get_connection();
build_seo_fact accepts dict, sqlite3.Row, and DictRow.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from shopifyseo.db import DictRow, table_columns
from shopifyseo.dashboard_queries._basic_fetchers import (
    _column_exists,
    _row_factory,
)
from shopifyseo.dashboard_queries._seo_facts import _row_as_dict, build_seo_fact


DASHBOARD_AREA = (
    Path("shopifyseo/dashboard_store.py"),
    *sorted(Path("shopifyseo/dashboard_queries").glob("*.py")),
    *sorted(Path("shopifyseo/dashboard_actions").glob("*.py")),
)


def _make_sqlite_row(data: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" * len(data))
    conn.execute(f"CREATE TABLE t ({cols})")
    conn.execute(f"INSERT INTO t VALUES ({placeholders})", tuple(data.values()))
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


def test_dashboard_area_has_no_sqlite3_imports_or_attrs():
    """AST: no import sqlite3 and no sqlite3.* attribute use in the dashboard area."""
    violations: list[str] = []
    for path in DASHBOARD_AREA:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3" or alias.name.startswith("sqlite3."):
                        violations.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlite3"):
                violations.append(f"{path}:{node.lineno}: from {node.module} import …")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "sqlite3":
                    violations.append(f"{path}:{node.lineno}: sqlite3.{node.attr}")
    if violations:
        listed = "\n  ".join(violations)
        raise AssertionError("sqlite3 still referenced in dashboard area:\n  " + listed)


def test_row_as_dict_accepts_dict_sqlite_row_and_dictrow():
    payload = {
        "handle": "x",
        "title": "Valid product title",
        "seo_title": "A valid SEO title of good length",
        "seo_description": "A valid description of the product and its specifications for an informed purchase.",
        "description_html": "Good specific product information. " * 25,
        "index_status": "Indexed",
        "index_flag": "",
    }
    sqlite_row = _make_sqlite_row(payload)
    dict_row = DictRow(payload)

    assert _row_as_dict(payload) is payload
    assert _row_as_dict(None) is None
    assert dict(_row_as_dict(sqlite_row))["handle"] == "x"
    assert dict(_row_as_dict(dict_row))["handle"] == "x"

    fact_from_dict = build_seo_fact("product", payload, None)
    fact_from_sqlite = build_seo_fact("product", sqlite_row, None)
    fact_from_dictrow = build_seo_fact("product", dict_row, None)
    assert fact_from_dict["handle"] == fact_from_sqlite["handle"] == fact_from_dictrow["handle"] == "x"
    assert fact_from_dict["score"] == fact_from_sqlite["score"] == fact_from_dictrow["score"]


def test_row_factory_leaves_existing_and_installs_dictrow_when_bare():
    existing = sqlite3.connect(":memory:")
    existing.row_factory = sqlite3.Row
    existing.execute("CREATE TABLE t (id INTEGER)")
    assert _row_factory(existing).row_factory is sqlite3.Row
    existing.close()

    bare = sqlite3.connect(":memory:")
    assert bare.row_factory is None
    wrapped = _row_factory(bare)
    wrapped.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    wrapped.execute("INSERT INTO t VALUES (1, 'a')")
    row = wrapped.execute("SELECT id, name FROM t").fetchone()
    assert isinstance(row, DictRow)
    assert row["id"] == 1
    assert row["NAME"] == "a"
    bare.close()


def test_column_exists_uses_table_columns(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE pages (handle TEXT, is_published INTEGER)")
    assert _column_exists(conn, "pages", "is_published") is True
    assert _column_exists(conn, "pages", "missing") is False
    assert "is_published" in table_columns(conn, "pages")
    conn.close()
