"""Remaining-area type/dialect cleanup (PostgreSQL PR 6c).

Guards the mechanical sqlite3 → shopifyseo.db swap in internal_links,
shopify_catalog_sync, dashboard_google, dashboard_ai_engine_parts,
embedding_store, and app-DB scripts. Behaviour with DATABASE_URL unset
must match main: live connections still use sqlite3.Row from get_connection().
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from shopifyseo.db import table_columns
from shopifyseo.internal_links.store import ensure_schema
from shopifyseo.shopify_catalog_sync.db import ensure_column


REMAINING_AREA = (
    *sorted(Path("shopifyseo/internal_links").rglob("*.py")),
    *sorted(Path("shopifyseo/shopify_catalog_sync").rglob("*.py")),
    *sorted(Path("shopifyseo/dashboard_google").rglob("*.py")),
    *sorted(Path("shopifyseo/dashboard_ai_engine_parts").rglob("*.py")),
    Path("shopifyseo/embedding_store.py"),
    *sorted(Path("scripts").glob("*.py")),
)


def test_remaining_area_has_no_sqlite3_imports_or_attrs():
    """AST: no import sqlite3 and no sqlite3.* attribute use in this area."""
    violations: list[str] = []
    for path in REMAINING_AREA:
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
        raise AssertionError("sqlite3 still referenced in remaining areas:\n  " + listed)


def test_link_suggestions_columns_via_helper():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE link_suggestions (id INTEGER, kind TEXT, ai_anchor_html TEXT)")
    ensure_schema(conn)
    cols = table_columns(conn, "link_suggestions")
    assert "ai_edit_json" in cols
    assert "id" in cols
    conn.close()


def test_catalog_ensure_column_uses_table_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE pages (handle TEXT)")
    ensure_column(conn, "pages", "is_published", "INTEGER")
    assert "is_published" in table_columns(conn, "pages")
    ensure_column(conn, "pages", "is_published", "INTEGER")
    assert "is_published" in table_columns(conn, "pages")
    conn.close()


def test_clusters_table_columns_via_helper():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE clusters (id INTEGER, name TEXT, priority_score REAL)")
    cols = table_columns(conn, "clusters")
    assert "priority_score" in cols
    assert "name" in cols
    assert "missing" not in cols
    conn.close()
