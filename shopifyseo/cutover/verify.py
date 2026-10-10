"""Count and value verification between a SQLite source and a Postgres target."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from shopifyseo.db import Backend, backend_for_connection, execute

from .catalog import INTEGER_EPOCH_COLUMNS, list_user_tables
from .real_columns import iter_real_affinity_columns


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


def _as_blob_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


def _blob_columns_sqlite(conn: Any, table: str) -> list[str]:
    cols = list(execute(conn, f'PRAGMA table_info("{table}")'))
    names = []
    for row in cols:
        ctype = str(row[2] if not hasattr(row, "keys") else row["type"] or "")
        if ctype.upper() == "BLOB":
            names.append(row[1] if not hasattr(row, "keys") else row["name"])
    return names


def _blob_columns_postgres(conn: Any, table: str) -> list[str]:
    rows = execute(
        conn,
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ? AND data_type = 'bytea'
        """,
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def _blob_fingerprint(conn: Any, table: str, column: str) -> tuple[int, int, str]:
    """Return (non-null count, total bytes, order-independent sha256 of per-row hashes)."""
    rows = execute(
        conn,
        f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL',
    ).fetchall()
    digests: list[tuple[int, str]] = []
    total = 0
    for row in rows:
        raw = _as_blob_bytes(row[0])
        total += len(raw)
        digests.append((len(raw), hashlib.sha256(raw).hexdigest()))
    digests.sort()
    outer = hashlib.sha256()
    for length, digest in digests:
        outer.update(f"{length}:{digest};".encode("ascii"))
    return len(digests), total, outer.hexdigest()


def _pg_lisp_blob_count(pg_conn: Any, table: str, column: str) -> int:
    """Rows whose bytea is pgloader's Lisp print form ``#(n n …)`` as text."""
    row = execute(
        pg_conn,
        f'''
        SELECT COUNT(*) FROM "{table}"
        WHERE "{column}" IS NOT NULL
          AND encode("{column}", 'escape') LIKE '#(%'
        ''',
    ).fetchone()
    return int(row[0]) if row else 0


def _verify_blob_columns(
    report: ValueReport,
    pg_conn: Any,
    sqlite_conn: Any | None,
) -> None:
    tables = set(list_user_tables(pg_conn))
    sqlite_tables = set(list_user_tables(sqlite_conn)) if sqlite_conn is not None else set()
    for table in sorted(tables):
        pg_cols = _blob_columns_postgres(pg_conn, table) if backend_for_connection(pg_conn) == Backend.POSTGRES else _blob_columns_sqlite(pg_conn, table)
        sqlite_cols = _blob_columns_sqlite(sqlite_conn, table) if sqlite_conn is not None and table in sqlite_tables else []
        for column in sorted(set(pg_cols) | set(sqlite_cols)):
            lisp_n = 0
            if backend_for_connection(pg_conn) == Backend.POSTGRES and column in pg_cols:
                lisp_n = _pg_lisp_blob_count(pg_conn, table, column)
                if lisp_n:
                    report.issues.append(
                        ValueIssue(
                            table,
                            column,
                            "blob_lisp_text",
                            lisp_n,
                            "bytea holds pgloader Lisp text #(n n …) instead of raw bytes; "
                            "reload with 'type blob to bytea using byte-vector-to-bytea'",
                        )
                    )
            if sqlite_conn is None or column not in sqlite_cols or column not in pg_cols:
                continue
            scount, slen, sdigest = _blob_fingerprint(sqlite_conn, table, column)
            pcount, plen, pdigest = _blob_fingerprint(pg_conn, table, column)
            if (scount, slen, sdigest) != (pcount, plen, pdigest):
                report.issues.append(
                    ValueIssue(
                        table,
                        column,
                        "blob_mismatch",
                        abs(slen - plen),
                        f"sqlite n={scount} bytes={slen} sha256={sdigest[:12]}…; "
                        f"postgres n={pcount} bytes={plen} sha256={pdigest[:12]}…",
                    )
                )


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


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"refusing unexpected identifier {name!r}")
    return f'"{name}"'


def _norm_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _pg_column_data_types(conn: Any, table: str) -> dict[str, str]:
    rows = execute(
        conn,
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ?
        """,
        (table,),
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _real_tuple_counts(conn: Any, table: str, cols: list[str]) -> Counter[tuple[Any, ...]]:
    sql = f'SELECT {", ".join(_ident(c) for c in cols)} FROM {_ident(table)}'
    counts: Counter[tuple[Any, ...]] = Counter()
    for row in execute(conn, sql):
        counts[tuple(_norm_float(row[i]) for i in range(len(cols)))] += 1
    return counts


def _verify_real_columns(
    report: ValueReport,
    pg_conn: Any,
    sqlite_conn: Any,
) -> None:
    """Assert PG double precision + exact float equality for SQLite REAL columns.

    Detects the cutover-2 bug (pgloader ``type real to real`` → 4-byte ``real``,
    ``gsc_position`` 6.682926829268292 → 6.6829267). Testdb schema rewrite
    already maps REAL → DOUBLE PRECISION, so this must compare a SQLite
    snapshot to the loaded catalog, not a harness-created schema.
    """
    pg_tables = set(list_user_tables(pg_conn))
    sqlite_tables = set(list_user_tables(sqlite_conn))
    by_table: dict[str, list[str]] = {}
    for table, column, _decl in iter_real_affinity_columns(sqlite_conn):
        if table not in sqlite_tables:
            continue
        by_table.setdefault(table, []).append(column)

    cells = 0
    mismatch_rows = 0
    checked_columns = 0
    is_pg = backend_for_connection(pg_conn) == Backend.POSTGRES

    for table, float_cols in sorted(by_table.items()):
        if table not in pg_tables:
            report.notes.append(
                f"sqlite REAL columns on {table} skipped (table missing on postgres)"
            )
            continue
        compare_cols = list(float_cols)
        if is_pg:
            types = _pg_column_data_types(pg_conn, table)
            present: list[str] = []
            for column in float_cols:
                data_type = types.get(column)
                if data_type is None:
                    report.issues.append(
                        ValueIssue(
                            table,
                            column,
                            "float_missing",
                            1,
                            "SQLite REAL-affinity column is absent on postgres",
                        )
                    )
                    continue
                if data_type != "double precision":
                    report.issues.append(
                        ValueIssue(
                            table,
                            column,
                            "float_not_double_precision",
                            1,
                            f"postgres data_type={data_type!r} (need double precision; "
                            "pgloader default real is 4-byte)",
                        )
                    )
                present.append(column)
            compare_cols = present
        if not compare_cols:
            continue
        float_cols = compare_cols
        left = _real_tuple_counts(sqlite_conn, table, float_cols)
        right = _real_tuple_counts(pg_conn, table, float_cols)
        cells += sum(left.values()) * len(float_cols)
        examples: list[str] = []
        table_mismatches = 0
        for key in set(left) | set(right):
            delta = abs(left[key] - right[key])
            if not delta:
                continue
            table_mismatches += delta
            if len(examples) < 5:
                examples.append(
                    f"tuple {key!r} sqlite={left[key]} postgres={right[key]}"
                )
        checked_columns += len(float_cols)
        if table_mismatches:
            mismatch_rows += table_mismatches
            report.issues.append(
                ValueIssue(
                    table,
                    ",".join(float_cols),
                    "float_mismatch",
                    table_mismatches,
                    "; ".join(examples) or "REAL values differ from the SQLite snapshot",
                )
            )

    if checked_columns:
        report.notes.append(
            f"{checked_columns} SQLite REAL-affinity columns compared "
            f"({cells} cells); {mismatch_rows} differing row-tuples"
        )


def verify_values(
    pg_conn: Any,
    *,
    sqlite_conn: Any | None = None,
    fail_on_orphans: bool = False,
) -> ValueReport:
    """Check mixed-type leftovers, REAL float precision, and optionally fail on orphans."""
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

    _verify_blob_columns(report, pg_conn, sqlite_conn)
    if sqlite_conn is not None:
        _verify_real_columns(report, pg_conn, sqlite_conn)

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
