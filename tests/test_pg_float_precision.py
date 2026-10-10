"""SQLite REAL columns must survive as Postgres double precision.

Dual-backend: testdb is SQLite when TEST_DATABASE_URL is unset, Postgres
when CI backend-postgres sets it. Never opens a live catalog.
"""

from __future__ import annotations

import math
import os
import shutil
import sqlite3
import subprocess

import pytest

from shopifyseo.cutover.load_file import (
    cast_rules,
    unquoted_whitespace_targets,
    validate_load_file,
)
from shopifyseo.cutover.real_columns import (
    PGLOADER_FLOAT_SOURCE_TYPES,
    bootstrap_sqlite_schema,
    iter_numeric_affinity_columns,
    pgloader_source_type,
    real_affinity_columns_from_repo_schema,
)
from shopifyseo.cutover.sql import CUTOVER_SQL_DIR
from shopifyseo.db import Backend, backend_for_connection, postgres_float_ddl
from shopifyseo.dashboard_store import ensure_dashboard_schema

# Values that 4-byte real cannot store (the cutover-2 regression).
POSITION = 6.682926829268292
CTR = 0.1
DURATION = 1e-9
WHOLE = 443.0


def test_postgres_float_ddl_rewrites_real_class_types():
    assert postgres_float_ddl("REAL") == "DOUBLE PRECISION"
    assert postgres_float_ddl("FLOAT") == "DOUBLE PRECISION"
    assert postgres_float_ddl("DOUBLE") == "DOUBLE PRECISION"
    assert postgres_float_ddl("DOUBLE PRECISION") == "DOUBLE PRECISION"
    assert postgres_float_ddl("REAL NOT NULL DEFAULT 0") == "DOUBLE PRECISION NOT NULL DEFAULT 0"
    assert postgres_float_ddl("INTEGER") == "INTEGER"
    assert postgres_float_ddl("TEXT") == "TEXT"


def test_load_file_casts_every_real_class_type_to_double_precision():
    rules = validate_load_file()
    joined = "\n".join(rules).lower()
    assert "type real to real" not in joined
    assert "type float to float" not in joined
    # Unquoted multi-word target is a parse error in pgloader 3.6.10.
    assert "to double precision" not in joined
    for src in PGLOADER_FLOAT_SOURCE_TYPES:
        token = f'type "{src}"' if src == "double precision" else f"type {src}"
        assert any(
            line.lower().startswith(token)
            and 'to "double precision"' in line.lower()
            and "using float-to-string" in line.lower()
            for line in rules
        ), (src, rules)
    assert "using float-to-string" in joined


def test_cast_targets_with_whitespace_must_be_quoted():
    load = (CUTOVER_SQL_DIR / "shopifyseo.load").read_text(encoding="utf-8")
    assert unquoted_whitespace_targets(cast_rules(load)) == []
    broken = [
        "CAST type integer to bigint drop typemod",
        "     type real to double precision drop typemod using float-to-string",
    ]
    assert unquoted_whitespace_targets(broken)


@pytest.mark.skipif(
    shutil.which("pgloader") is None or shutil.which("timeout") is None,
    reason="pgloader/timeout not on PATH",
)
def test_load_file_pgloader_parses_without_esrap(tmp_path):
    sqlite = tmp_path / "tiny.sqlite3"
    sqlite3.connect(sqlite).close()
    src = (CUTOVER_SQL_DIR / "shopifyseo.load").read_text(encoding="utf-8")
    rendered = src.replace("__SQLITE_URI__", f"sqlite:///{sqlite}")
    rendered = rendered.replace("__POSTGRES_URI__", "postgresql://x@127.0.0.1:1/x")
    load = tmp_path / "parse.load"
    load.write_text(rendered, encoding="utf-8")
    proc = subprocess.run(
        ["timeout", "-s", "KILL", "30", "pgloader", str(load)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "ESRAP-PARSE-ERROR" not in out, out


def test_validate_load_file_rejects_unquoted_double_precision_target(tmp_path):
    broken = tmp_path / "broken.load"
    broken.write_text(
        "\n".join(
            [
                "CAST type integer to bigint drop typemod,",
                "     type real to double precision drop typemod using float-to-string,",
                '     type float to "double precision" drop typemod using float-to-string,',
                '     type double to "double precision" drop typemod using float-to-string,',
                '     type "double precision" to "double precision" drop typemod using float-to-string',
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        validate_load_file(broken)
    except ValueError as exc:
        assert "unquoted" in str(exc).lower() or "esrap" in str(exc).lower()
    else:
        raise AssertionError("unquoted CAST target must fail validation")


def test_schema_real_columns_are_all_covered_by_load_casts():
    columns = real_affinity_columns_from_repo_schema()
    assert columns, "expected REAL-affinity columns in the bootstrapped schema"
    rules = "\n".join(cast_rules((CUTOVER_SQL_DIR / "shopifyseo.load").read_text())).lower()
    missing: list[tuple[str, str, str]] = []
    source_types = {pgloader_source_type(decl) for _t, _c, decl in columns}
    for src in source_types:
        quoted_src = f'type "{src}"' if " " in src else f"type {src}"
        needle = f'{quoted_src} to "double precision"'
        if needle not in rules:
            missing.extend(
                (table, name, decl)
                for table, name, decl in columns
                if pgloader_source_type(decl) == src
            )
    assert not missing, (
        "REAL-affinity columns whose declared type is not CAST to "
        '"double precision": '
        f"{missing[:12]}"
    )
    assert "gsc_position" in {c for _t, c, _d in columns}
    assert "gsc_ctr" in {c for _t, c, _d in columns}
    assert "ga4_avg_session_duration" in {c for _t, c, _d in columns}


def test_schema_has_no_numeric_affinity_float_columns():
    """NUMERIC affinity is not used for floats in this schema; do not CAST it."""
    saved = os.environ.pop("DATABASE_URL", None)
    try:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "schema.sqlite3")
            try:
                bootstrap_sqlite_schema(conn)
                numeric = iter_numeric_affinity_columns(conn)
            finally:
                conn.close()
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved
    floatish = [
        (table, name, decl)
        for table, name, decl in numeric
        if any(token in (decl or "").upper() for token in ("NUM", "DEC", "REAL", "FLOA", "DOUB"))
    ]
    assert floatish == [], floatish


def test_real_columns_round_trip_full_precision(db_conn):
    ensure_dashboard_schema(db_conn)
    db_conn.execute(
        """
        INSERT INTO products (
            shopify_id, title, handle, tags_json, options_json, raw_json, synced_at,
            gsc_position, gsc_ctr, ga4_avg_session_duration
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gid://shopify/Product/precision",
            "Precision",
            "precision-product",
            "[]",
            "[]",
            "{}",
            "2026-10-10 00:00:00",
            POSITION,
            CTR,
            DURATION,
        ),
    )
    db_conn.commit()
    row = db_conn.execute(
        "SELECT gsc_position, gsc_ctr, ga4_avg_session_duration FROM products WHERE handle = ?",
        ("precision-product",),
    ).fetchone()
    assert row["gsc_position"] == POSITION
    assert row["gsc_ctr"] == CTR
    assert row["ga4_avg_session_duration"] == DURATION

    db_conn.execute(
        "UPDATE products SET ga4_avg_session_duration = ? WHERE handle = ?",
        (WHOLE, "precision-product"),
    )
    db_conn.commit()
    whole = db_conn.execute(
        "SELECT ga4_avg_session_duration FROM products WHERE handle = ?",
        ("precision-product",),
    ).fetchone()[0]
    # 443 vs 443.0 on live APIs is SUM(bigint) → PG numeric loaded as float
    # (shopifyseo/db/pg_runtime.py), not SQLite REAL affinity. REAL columns
    # already return floats. Same numeric value — do not change the API shape.
    assert float(whole) == WHOLE
    assert math.isclose(float(whole), 443.0, rel_tol=0.0, abs_tol=0.0)

    if backend_for_connection(db_conn) == Backend.POSTGRES:
        expected = {(t, c) for t, c, _d in real_affinity_columns_from_repo_schema()}
        rows = db_conn.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
            """
        ).fetchall()
        got = {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}
        wrong = []
        for table, column in expected:
            data_type = got.get((table, column))
            if data_type is None:
                continue
            if data_type != "double precision":
                wrong.append((table, column, data_type))
        assert wrong == [], wrong
