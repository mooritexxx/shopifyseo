"""SQL-file helpers and optional cluster_keywords orphan cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shopifyseo.db import execute

REPO_ROOT = Path(__file__).resolve().parents[2]
CUTOVER_SQL_DIR = REPO_ROOT / "scripts" / "pg_cutover"


def _split_sql_statements(script: str) -> list[str]:
    """Split a SQL script on semicolons, respecting dollar-quotes and strings."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    in_single = False
    dollar: str | None = None
    while i < n:
        ch = script[i]
        if dollar:
            if script.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            buf.append(ch)
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and script[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] == "_"):
                j += 1
            if j < n and script[j] == "$":
                dollar = script[i : j + 1]
                buf.append(dollar)
                i = j + 1
                continue
        if ch == "-" and i + 1 < n and script[i + 1] == "-":
            nl = script.find("\n", i)
            i = n if nl < 0 else nl + 1
            buf.append("\n")
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_sql_file(pg_conn: Any, path: str | Path) -> None:
    """Execute a SQL file on a psycopg connection (autocommit-friendly)."""
    script = Path(path).read_text(encoding="utf-8")
    if not hasattr(pg_conn, "execute"):
        raise TypeError("apply_sql_file expects a psycopg connection")
    # One statement at a time (psycopg 3). No bound params so %I in DO
    # format() strings is left alone. testdb's adapter does not rewrite
    # DO-blocks (not CREATE/ALTER DDL heads).
    for statement in _split_sql_statements(script):
        execute(pg_conn, statement)
    if not getattr(pg_conn, "autocommit", False):
        pg_conn.commit()


def cluster_keywords_orphan_sql(*, delete: bool) -> str:
    """SELECT (default) or DELETE orphan cluster_keywords rows."""
    predicate = (
        "NOT EXISTS (SELECT 1 FROM clusters c WHERE c.id = cluster_keywords.cluster_id)"
    )
    if delete:
        return f"DELETE FROM cluster_keywords WHERE {predicate}"
    return f"SELECT COUNT(*) FROM cluster_keywords WHERE {predicate}"


def delete_cluster_keyword_orphans(pg_conn: Any) -> int:
    """Delete cluster_keywords rows whose cluster_id is missing. Explicit only."""
    cur = execute(
        pg_conn,
        """
        DELETE FROM cluster_keywords ck
        WHERE NOT EXISTS (SELECT 1 FROM clusters c WHERE c.id = ck.cluster_id)
        """,
    )
    n = cur.rowcount if cur.rowcount is not None else 0
    if not getattr(pg_conn, "autocommit", False):
        pg_conn.commit()
    return int(n)


def validate_named_constraint(pg_conn: Any, table: str, constraint: str) -> None:
    """VALIDATE CONSTRAINT — fails if existing rows violate the FK."""
    execute(pg_conn, f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{constraint}"')
    if not getattr(pg_conn, "autocommit", False):
        pg_conn.commit()
