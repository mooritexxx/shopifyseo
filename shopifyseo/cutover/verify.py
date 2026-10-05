"""Count and value verification between a SQLite source and a Postgres target."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shopifyseo.db import Backend, backend_for_connection, execute

from .catalog import INTEGER_EPOCH_COLUMNS, list_user_tables


@dataclass
class CountMismatch:
    table: str
    sqlite_count: int
    postgres_count: int


@dataclass
class CountReport:
    matches: dict[str, int] = field(default_factory=dict)
    mismatches: list[CountMismatch] = field(default_factory=list)
    sqlite_only: list[str] = field(default_factory=list)
    postgres_only: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.sqlite_only and not self.postgres_only


@dataclass
class ValueIssue:
    table: str
    column: str
    kind: str
    count: int
    detail: str = ""


@dataclass
class ValueReport:
    issues: list[ValueIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _count(conn: Any, table: str) -> int:
    row = execute(conn, f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int(row[0])


def verify_counts(sqlite_conn: Any, pg_conn: Any) -> CountReport:
    """Compare user-table row counts. Tables missing on one side are reported."""
    left = set(list_user_tables(sqlite_conn))
    right = set(list_user_tables(pg_conn))
    report = CountReport()
    report.sqlite_only = sorted(left - right)
    report.postgres_only = sorted(right - left)
    for table in sorted(left & right):
        sc = _count(sqlite_conn, table)
        pc = _count(pg_conn, table)
        if sc == pc:
            report.matches[table] = sc
        else:
            report.mismatches.append(CountMismatch(table, sc, pc))
    return report


def _pg_text_in_int_count(pg_conn: Any, table: str, column: str) -> int | None:
    """Return how many values are non-numeric text, or None if the column is absent."""
    if backend_for_connection(pg_conn) != Backend.POSTGRES:
        leftover = _sqlite_text_in_int_count(pg_conn, table, column)
        return leftover
    exists = execute(
        pg_conn,
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ? AND column_name = ?
        """,
        (table, column),
    ).fetchone()
    if not exists:
        return None
    dtype = exists[0]
    if dtype in ("integer", "bigint", "smallint"):
        return 0
    row = execute(
        pg_conn,
        f'''
        SELECT COUNT(*) FROM "{table}"
        WHERE "{column}" IS NOT NULL
          AND TRIM("{column}"::text) <> ''
          AND TRIM("{column}"::text) !~ '^-?[0-9]+$'
        ''',
    ).fetchone()
    return int(row[0])


def _sqlite_text_in_int_count(conn: Any, table: str, column: str) -> int | None:
    present = execute(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if not present:
        return None
    cols = [row[1] for row in execute(conn, f'PRAGMA table_info("{table}")')]
    if column not in cols:
        return None
    row = execute(
        conn,
        f'''SELECT COUNT(*) FROM "{table}" WHERE typeof("{column}") = 'text' ''',
    ).fetchone()
    return int(row[0])


def cluster_keyword_orphan_count(conn: Any) -> int | None:
    tables = set(list_user_tables(conn))
    if "cluster_keywords" not in tables or "clusters" not in tables:
        return None
    row = execute(
        conn,
        """
        SELECT COUNT(*) FROM cluster_keywords ck
        WHERE NOT EXISTS (SELECT 1 FROM clusters c WHERE c.id = ck.cluster_id)
        """,
    ).fetchone()
    return int(row[0])


def verify_values(
    pg_conn: Any,
    *,
    sqlite_conn: Any | None = None,
    fail_on_orphans: bool = False,
) -> ValueReport:
    """Check mixed-type leftovers and optionally fail on cluster_keywords orphans."""
    report = ValueReport()
    for table, columns in INTEGER_EPOCH_COLUMNS.items():
        for column in columns:
            n = _pg_text_in_int_count(pg_conn, table, column)
            if n is None:
                continue
            if n:
                report.issues.append(
                    ValueIssue(
                        table,
                        column,
                        "text_in_int",
                        n,
                        "non-numeric values remain in a declared integer column",
                    )
                )
            if sqlite_conn is not None:
                leftover = _sqlite_text_in_int_count(sqlite_conn, table, column)
                if leftover:
                    report.notes.append(
                        f"sqlite {table}.{column} still has {leftover} text values "
                        "(pre-fix the working copy before load)"
                    )

    orphans = cluster_keyword_orphan_count(pg_conn)
    if orphans is None:
        report.notes.append("cluster_keywords/clusters not present; orphan check skipped")
    elif orphans:
        msg = (
            f"{orphans} cluster_keywords rows reference a missing clusters.id "
            "(kept by default; pass --delete-cluster-orphans to remove)"
        )
        if fail_on_orphans:
            report.issues.append(
                ValueIssue("cluster_keywords", "cluster_id", "orphan", orphans, msg)
            )
        else:
            report.notes.append(msg)
    else:
        report.notes.append("cluster_keywords has no orphan cluster_id rows")
    return report
