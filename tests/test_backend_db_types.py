"""Backend services/routers type/dialect cleanup (PostgreSQL PR 6b).

Guards the mechanical sqlite3 → shopifyseo.db swap under backend/app/services
and backend/app/routers. Behaviour with DATABASE_URL unset must match main:
live connections still use sqlite3.Row from get_connection(); opportunities
_row_factory leaves an existing mapping factory and installs DictRow only on
a bare connection.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from backend.app.services.keyword_clustering._storage import (
    _cluster_planning_from_row,
    _cluster_stats_from_row,
)
from backend.app.services.opportunities_service import _row_factory
from shopifyseo.db import DictRow, table_columns


SERVICES_ROUTERS_AREA = (
    *sorted(Path("backend/app/services").rglob("*.py")),
    *sorted(Path("backend/app/routers").rglob("*.py")),
)


def _make_sqlite_row(data: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"{k} TEXT" for k in data)
    placeholders = ", ".join("?" * len(data))
    conn.execute(f"CREATE TABLE t ({cols})")
    conn.execute(f"INSERT INTO t VALUES ({placeholders})", tuple(data.values()))
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


def test_services_routers_have_no_sqlite3_imports_or_attrs():
    """AST: no import sqlite3 and no sqlite3.* attribute use in this area."""
    violations: list[str] = []
    for path in SERVICES_ROUTERS_AREA:
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
        raise AssertionError("sqlite3 still referenced in services/routers:\n  " + listed)


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


def test_cluster_row_helpers_accept_sqlite_row_and_dictrow():
    payload = {
        "dominant_serp_features": "featured_snippet",
        "content_format_hints": "how-to",
        "avg_cps": "1.5",
        "detected_entity": "Widget",
        "cluster_intent": "informational",
        "cluster_role": "hub",
        "cannibalization_risk": "low",
        "quality_score": "0.8",
        "core_keywords_json": '["widget"]',
        "supporting_keywords_json": "[]",
        "extended_keywords_json": "[]",
    }
    sqlite_row = _make_sqlite_row(payload)
    dict_row = DictRow(payload)

    stats_sqlite = _cluster_stats_from_row(sqlite_row)
    stats_dict = _cluster_stats_from_row(dict_row)
    assert stats_sqlite["dominant_serp_features"] == stats_dict["dominant_serp_features"] == "featured_snippet"
    assert stats_sqlite["content_format_hints"] == stats_dict["content_format_hints"] == "how-to"
    assert stats_sqlite["avg_cps"] == stats_dict["avg_cps"] == 1.5

    plan_sqlite = _cluster_planning_from_row(sqlite_row)
    plan_dict = _cluster_planning_from_row(dict_row)
    assert plan_sqlite["detected_entity"] == plan_dict["detected_entity"] == "Widget"
    assert plan_sqlite["cluster_intent"] == plan_dict["cluster_intent"] == "informational"
    assert plan_sqlite["cannibalization_risk"] == plan_dict["cannibalization_risk"] == "low"
    assert plan_sqlite["quality_score"] == plan_dict["quality_score"] == 0.8
    assert plan_sqlite["core_keywords"] == plan_dict["core_keywords"] == ["widget"]


def test_cluster_table_columns_via_helper():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE clusters (id INTEGER, name TEXT, priority_score REAL)")
    cols = table_columns(conn, "clusters")
    assert "priority_score" in cols
    assert "name" in cols
    assert "missing" not in cols
    conn.close()
