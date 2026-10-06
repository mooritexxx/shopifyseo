"""Plan 8 cutover tooling: syntax, data fixes, orphan default, rollback copy."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shopifyseo.cutover.catalog import INTEGER_EPOCH_COLUMNS
from shopifyseo.cutover.delta import (
    LiveSqliteRefused,
    TableDelta,
    apply_delta_to_sqlite_copy,
    export_pg_delta,
    refuse_live_sqlite,
    write_cutover_mark,
)
from shopifyseo.cutover.sequences import resync_cutover_identities
from shopifyseo.cutover.sql import (
    CUTOVER_SQL_DIR,
    _split_sql_statements,
    apply_sql_file,
    cluster_keywords_orphan_sql,
    delete_cluster_keyword_orphans,
)
from shopifyseo.cutover.sqlite_pre_fix import fix_sqlite_copy
from shopifyseo.cutover.verify import cluster_keyword_orphan_count, verify_counts, verify_values
from shopifyseo.db import Backend, backend_for_connection

ROOT = Path(__file__).resolve().parents[1]
CUTOVER_SH = ROOT / "scripts" / "pg_cutover.sh"
ENSURE_SH = ROOT / "scripts" / "ensure-postgres.sh"
DELTA_PY = ROOT / "scripts" / "pg_to_sqlite_delta.py"
DOCS = ROOT / "docs" / "pg-cutover.md"


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        text=True,
        capture_output=True,
        **kwargs,
    )


def test_cutover_scripts_exist_and_are_executable():
    for path in (CUTOVER_SH, ENSURE_SH, DELTA_PY):
        assert path.is_file(), path
        assert os.access(path, os.X_OK), f"{path} should be executable"


def test_bash_syntax_of_shell_entrypoints():
    for path in (CUTOVER_SH, ENSURE_SH):
        proc = _run(["bash", "-n", str(path)])
        assert proc.returncode == 0, proc.stderr


def test_help_does_not_flip_live_database_url():
    proc = _run(["bash", str(CUTOVER_SH), "--help"])
    assert proc.returncode == 0
    text = proc.stdout + proc.stderr
    assert "never exports DATABASE_URL" in text or "DATABASE_URL" in text
    assert "--delete-cluster-orphans" in text
    assert "--dry-run" in text


def test_load_file_omits_foreign_keys_and_has_placeholders():
    load = (CUTOVER_SQL_DIR / "shopifyseo.load").read_text()
    assert "__SQLITE_URI__" in load
    assert "__POSTGRES_URI__" in load
    assert "no foreign keys" in load
    assert "type blob to bytea using byte-vector-to-bytea" in load
    assert "keyword_metrics.updated_at" in load
    assert "PASSWORD" not in load
    assert "postgresql://shopifyseo:" not in load


def test_sql_splitter_keeps_dollar_quoted_blocks():
    statements = _split_sql_statements(
        """
        -- comment
        CREATE INDEX IF NOT EXISTS foo ON t (x);
        DO $$
        BEGIN
            IF to_regclass('t') IS NOT NULL THEN
                EXECUTE 'SELECT 1; SELECT 2';
            END IF;
        END
        $$;
        ANALYZE;
        """
    )
    assert len(statements) == 3
    assert statements[0].startswith("CREATE INDEX")
    assert statements[1].startswith("DO $$")
    assert "SELECT 1; SELECT 2" in statements[1]
    assert statements[2] == "ANALYZE"


def test_shipped_sql_files_split_into_runnable_statements():
    for name in ("post_load_fixups.sql", "post_load_constraints.sql"):
        statements = _split_sql_statements((CUTOVER_SQL_DIR / name).read_text())
        assert statements, name
        assert all(s.strip() for s in statements)


def test_constraint_sql_is_not_valid_and_does_not_delete_orphans():
    sql = (CUTOVER_SQL_DIR / "post_load_constraints.sql").read_text()
    assert "NOT VALID" in sql
    assert "cluster_keywords_cluster_id_fkey" in sql
    assert "idx_keyword_metrics_keyword_lower" in sql
    assert "DELETE FROM" not in sql.upper().replace("\n", " ")
    fixups = (CUTOVER_SQL_DIR / "post_load_fixups.sql").read_text()
    assert "cluster_keywords" not in fixups or "DELETE" not in fixups
    assert "keyword_metrics" in fixups


def test_orphan_delete_sql_only_from_explicit_helper():
    assert "DELETE FROM cluster_keywords" in cluster_keywords_orphan_sql(delete=True)
    assert "SELECT COUNT(*)" in cluster_keywords_orphan_sql(delete=False)
    script = CUTOVER_SH.read_text()
    assert "--delete-cluster-orphans" in script
    # Default path reports orphans; delete is gated on DELETE_ORPHANS.
    assert 'if [[ "$DELETE_ORPHANS" -eq 1 ]]' in script


def test_docs_say_live_stays_sqlite():
    text = DOCS.read_text()
    assert "Live deploy stays on SQLite" in text
    assert "does not flip" in text.lower() or "does **not** set live `DATABASE_URL`" in text
    assert "pg.env" in text
    assert "--delete-cluster-orphans" in text
    assert "pg_to_sqlite_delta" in text
    assert "ensure-postgres.sh" in text
    assert "never commit" in text.lower() or "Never commit" in text


def test_pg_env_example_has_no_secrets():
    example = (CUTOVER_SQL_DIR / "pg_env.example").read_text()
    assert "CUTOVER_DATABASE_URL" in example
    assert "PGPASSWORD=" in example
    assert "supersecret" not in example.lower()
    assert not any(
        line.split("#", 1)[0].strip().startswith("DATABASE_URL=")
        for line in example.splitlines()
    )


def _seed_sqlite_mixed(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY,
            name TEXT,
            generated_at TEXT
        );
        CREATE TABLE cluster_keywords (
            cluster_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            PRIMARY KEY (cluster_id, keyword)
        );
        CREATE TABLE keyword_metrics (
            keyword TEXT PRIMARY KEY,
            volume INTEGER,
            difficulty INTEGER,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO clusters (id, name, generated_at) VALUES (1, 'kept', '2026-01-01 00:00:00');
        INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (1, 'alpha');
        INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (99, 'orphan');
        INSERT INTO keyword_metrics (keyword, volume, difficulty, updated_at)
            VALUES ('alpha', 10, 20, '2026-01-02 03:04:05');
        INSERT INTO keyword_metrics (keyword, volume, difficulty, updated_at)
            VALUES ('beta', '', 3, 1700000000);
        """
    )
    conn.commit()
    conn.close()


def test_sqlite_pre_fix_converts_updated_at_text_and_empty_numeric(tmp_path):
    db = tmp_path / "copy.sqlite3"
    _seed_sqlite_mixed(db)
    report = fix_sqlite_copy(db)
    assert report["epoch_text"]["keyword_metrics.updated_at"] == 1
    assert report["empty_numeric"]["keyword_metrics.volume"] == 1
    conn = sqlite3.connect(db)
    rows = {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute("SELECT keyword, volume, difficulty, updated_at FROM keyword_metrics")
    }
    conn.close()
    assert rows["alpha"][2] == int(
        datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
    )
    assert rows["beta"][0] is None
    assert rows["beta"][2] == 1700000000


def test_verify_counts_and_orphan_report(tmp_path):
    left = tmp_path / "left.sqlite3"
    right = tmp_path / "right.sqlite3"
    _seed_sqlite_mixed(left)
    _seed_sqlite_mixed(right)
    a = sqlite3.connect(left)
    b = sqlite3.connect(right)
    try:
        report = verify_counts(a, b)
        assert report.ok
        assert report.matches["keyword_metrics"] == 2
        assert cluster_keyword_orphan_count(a) == 1
        soft = verify_values(a, sqlite_conn=a, fail_on_orphans=False)
        assert cluster_keyword_orphan_count(a) == 1
        assert any("orphan" in n for n in soft.notes)
        hard = verify_values(a, fail_on_orphans=True)
        assert any(i.kind == "orphan" for i in hard.issues)
    finally:
        a.close()
        b.close()


def test_verify_blob_length_and_sha256(tmp_path):
    import hashlib
    import struct

    left = tmp_path / "left.sqlite3"
    right = tmp_path / "right.sqlite3"
    blob = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
    for path in (left, right):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, embedding BLOB)")
        conn.execute("INSERT INTO embeddings (id, embedding) VALUES (1, ?)", (blob,))
        conn.commit()
        conn.close()
    a = sqlite3.connect(left)
    b = sqlite3.connect(right)
    try:
        report = verify_values(b, sqlite_conn=a)
        assert not any(i.kind.startswith("blob") for i in report.issues)
        b.execute("UPDATE embeddings SET embedding = ?", (blob + b"x",))
        b.commit()
        report = verify_values(b, sqlite_conn=a)
        assert any(i.kind == "blob_mismatch" for i in report.issues)
    finally:
        a.close()
        b.close()
    assert hashlib.sha256(blob).digest()


def test_verify_counts_mismatch(tmp_path):
    left = tmp_path / "left.sqlite3"
    right = tmp_path / "right.sqlite3"
    _seed_sqlite_mixed(left)
    _seed_sqlite_mixed(right)
    b = sqlite3.connect(right)
    b.execute("DELETE FROM keyword_metrics WHERE keyword = 'beta'")
    b.commit()
    a = sqlite3.connect(left)
    try:
        report = verify_counts(a, b)
        assert not report.ok
        tables = {m.table for m in report.mismatches}
        assert "keyword_metrics" in tables
    finally:
        a.close()
        b.close()


def test_delta_refuses_live_sqlite_name(tmp_path, monkeypatch):
    live = tmp_path / "shopify_catalog.sqlite3"
    live.write_bytes(b"")
    with pytest.raises(LiveSqliteRefused):
        refuse_live_sqlite(live)
    monkeypatch.setenv("SHOPIFY_CATALOG_DB_PATH", str(tmp_path / "custom.sqlite3"))
    custom = tmp_path / "custom.sqlite3"
    custom.write_bytes(b"")
    with pytest.raises(LiveSqliteRefused):
        refuse_live_sqlite(custom)
    allowed = refuse_live_sqlite(live, allow_live=True)
    assert allowed == live.resolve()


def test_delta_applies_to_copy_not_source(tmp_path):
    src = tmp_path / "src.sqlite3"
    dest = tmp_path / "dest.copy.sqlite3"
    _seed_sqlite_mixed(src)
    import shutil

    shutil.copy2(src, dest)
    before = sqlite3.connect(src)
    before.execute("UPDATE keyword_metrics SET updated_at = 1 WHERE keyword = 'alpha'")
    before.commit()
    before.close()
    apply_delta_to_sqlite_copy(
        dest,
        [
            TableDelta(
                table="keyword_metrics",
                rows=[
                    {
                        "keyword": "alpha",
                        "volume": 99,
                        "difficulty": 20,
                        "updated_at": 2_000_000_000,
                    }
                ],
            )
        ],
    )
    dest_conn = sqlite3.connect(dest)
    row = dest_conn.execute(
        "SELECT volume, updated_at FROM keyword_metrics WHERE keyword = 'alpha'"
    ).fetchone()
    dest_conn.close()
    src_conn = sqlite3.connect(src)
    src_row = src_conn.execute(
        "SELECT volume, updated_at FROM keyword_metrics WHERE keyword = 'alpha'"
    ).fetchone()
    src_conn.close()
    assert row == (99, 2_000_000_000)
    assert src_row[0] != 99


def test_write_cutover_mark(tmp_path):
    path = tmp_path / "cutover_mark.json"
    payload = write_cutover_mark(
        path,
        cutover_at=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        sqlite_backup="/tmp/copy.sqlite3",
        postgres_target="postgresql://shopifyseo@127.0.0.1/shopifyseo",
    )
    loaded = json.loads(path.read_text())
    assert loaded["cutover_epoch"] == payload["cutover_epoch"]
    assert loaded["cutover_at"].startswith("2026-10-05T12:00:00")


def test_delta_cli_refuses_live(tmp_path):
    mark = tmp_path / "cutover_mark.json"
    write_cutover_mark(mark, sqlite_backup=str(tmp_path / "x.sqlite3"))
    live = tmp_path / "shopify_catalog.sqlite3"
    live.write_bytes(b"")
    proc = _run(
        [
            sys.executable,
            str(DELTA_PY),
            "--mark",
            str(mark),
            "--sqlite-copy",
            str(live),
            "--postgres-url",
            "postgresql://shopifyseo@127.0.0.1/shopifyseo_cutover_dryrun",
        ]
    )
    assert proc.returncode == 2
    assert "refusing to write" in proc.stderr


def test_integer_epoch_catalog_includes_keyword_metrics():
    assert "updated_at" in INTEGER_EPOCH_COLUMNS["keyword_metrics"]


@pytest.mark.postgres
def test_postgres_fixups_constraints_orphans_and_identity(testdb):
    if testdb.backend != Backend.POSTGRES:
        pytest.skip("TEST_DATABASE_URL is not Postgres")
    conn = testdb.connect()
    try:
        assert backend_for_connection(conn) == Backend.POSTGRES
        conn.execute(
            """
            CREATE TABLE clusters (
                id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                name TEXT,
                generated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE cluster_keywords (
                cluster_id BIGINT NOT NULL,
                keyword TEXT NOT NULL,
                PRIMARY KEY (cluster_id, keyword)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE keyword_metrics (
                keyword TEXT PRIMARY KEY,
                volume BIGINT,
                updated_at TEXT
            )
            """
        )
        conn.execute("INSERT INTO clusters (id, name, generated_at) VALUES (1, 'kept', 't')")
        conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (1, 'alpha')")
        conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (99, 'orphan')")
        conn.execute(
            "INSERT INTO keyword_metrics (keyword, volume, updated_at) VALUES ('alpha', 1, '2026-01-02 03:04:05')"
        )

        apply_sql_file(conn, CUTOVER_SQL_DIR / "post_load_fixups.sql")
        dtype = conn.execute(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'keyword_metrics' AND column_name = 'updated_at'
            """
        ).fetchone()[0]
        assert dtype in ("bigint", "integer")
        updated = conn.execute(
            "SELECT updated_at FROM keyword_metrics WHERE keyword = 'alpha'"
        ).fetchone()[0]
        assert int(updated) == int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())

        assert cluster_keyword_orphan_count(conn) == 1
        soft = verify_values(conn, fail_on_orphans=False)
        assert soft.ok
        assert any("orphan" in n for n in soft.notes)
        hard = verify_values(conn, fail_on_orphans=True)
        assert not hard.ok

        apply_sql_file(conn, CUTOVER_SQL_DIR / "post_load_constraints.sql")
        con = conn.execute(
            """
            SELECT c.convalidated
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.conname = 'cluster_keywords_cluster_id_fkey'
              AND n.nspname = current_schema()
            """
        ).fetchone()
        assert con is not None
        assert con[0] is False

        # Default: orphans remain after constraints.
        assert cluster_keyword_orphan_count(conn) == 1
        deleted = delete_cluster_keyword_orphans(conn)
        assert deleted == 1
        assert cluster_keyword_orphan_count(conn) == 0

        conn.execute("INSERT INTO clusters (id, name, generated_at) VALUES (50, 'seq', 't')")
        results = resync_cutover_identities(conn)
        by_table = {r.table: r for r in results}
        assert "clusters" in by_table
        assert by_table["clusters"].error is None
        assert by_table["clusters"].new_value == 51
        if "seo_change_events" in by_table:
            assert by_table["seo_change_events"].error and "skipped" in by_table["seo_change_events"].error
    finally:
        conn.close()


@pytest.mark.postgres
def test_postgres_delta_export_newer_than_mark(testdb):
    if testdb.backend != Backend.POSTGRES:
        pytest.skip("TEST_DATABASE_URL is not Postgres")
    conn = testdb.connect()
    try:
        conn.execute(
            """
            CREATE TABLE keyword_metrics (
                keyword TEXT PRIMARY KEY,
                volume BIGINT,
                difficulty BIGINT,
                updated_at BIGINT NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO keyword_metrics (keyword, volume, difficulty, updated_at) VALUES ('old', 1, 1, 100)"
        )
        conn.execute(
            "INSERT INTO keyword_metrics (keyword, volume, difficulty, updated_at) VALUES ('new', 2, 2, 500)"
        )
        mark = {"cutover_epoch": 200, "cutover_at": "1970-01-01T00:03:20Z"}
        deltas = export_pg_delta(conn, mark, tables=["keyword_metrics"])
        by_table = {d.table: d for d in deltas}
        assert [row["keyword"] for row in by_table["keyword_metrics"].rows] == ["new"]
    finally:
        conn.close()
