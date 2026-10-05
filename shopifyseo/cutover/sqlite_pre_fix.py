"""Pre-pgloader fixes on a *copy* of the SQLite catalog.

SQLite affinity lets INTEGER columns hold text. pgloader then fails or
promotes the column to text. Known case: ``keyword_metrics.updated_at``
(declared INTEGER epoch, some rows store ``CURRENT_TIMESTAMP`` text).

Also drops SQLite ``LOWER(keyword)`` expression indexes that pgloader 3.6
cannot migrate; ``post_load_constraints.sql`` recreates them on Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shopifyseo.db import connect_sqlite, execute, table_exists

from .catalog import INTEGER_EPOCH_COLUMNS


def _parse_epoch(value: Any) -> int | None:
    """Best-effort conversion of mixed SQLite values to unix seconds."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    try:
        return int(float(text))
    except ValueError:
        pass
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _quoted(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _pk_columns(conn: Any, table: str) -> list[str]:
    info = list(execute(conn, f"PRAGMA table_info({_quoted(table)})"))
    ranked = [(int(row[5]), row[1]) for row in info if row[5]]
    ranked.sort()
    return [name for _ord, name in ranked]


def fix_empty_strings_in_numeric_columns(conn: Any) -> dict[str, int]:
    """Set empty-string values in INTEGER/REAL columns to NULL (or 0 if NOT NULL)."""
    changed: dict[str, int] = {}
    tables = [
        row[0]
        for row in execute(
            conn,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
        )
    ]
    for table in tables:
        info = list(execute(conn, f"PRAGMA table_info({_quoted(table)})"))
        for _cid, name, decl, notnull, default, _pk in info:
            kind = (decl or "").upper()
            if not any(token in kind for token in ("INT", "REAL", "FLOA", "DOUB", "NUM")):
                continue
            qtable, qcol = _quoted(table), _quoted(name)
            if notnull:
                fallback = 0 if default is None else default
                cur = execute(
                    conn,
                    f"UPDATE {qtable} SET {qcol} = ? WHERE typeof({qcol}) = 'text' AND trim({qcol}) = ''",
                    (fallback,),
                )
            else:
                cur = execute(
                    conn,
                    f"UPDATE {qtable} SET {qcol} = NULL WHERE typeof({qcol}) = 'text' AND trim({qcol}) = ''",
                )
            if cur.rowcount:
                changed[f"{table}.{name}"] = changed.get(f"{table}.{name}", 0) + cur.rowcount
    return changed


def fix_integer_epoch_columns(conn: Any) -> dict[str, int]:
    """Rewrite text-in-int timestamp columns to unix seconds (0 if unparseable + NOT NULL)."""
    changed: dict[str, int] = {}
    for table, columns in INTEGER_EPOCH_COLUMNS.items():
        if not table_exists(conn, table):
            continue
        info = {row[1]: row for row in execute(conn, f"PRAGMA table_info({_quoted(table)})")}
        pks = _pk_columns(conn, table)
        if not pks:
            continue
        qtable = _quoted(table)
        q_pks = ", ".join(_quoted(pk) for pk in pks)
        where_pk = " AND ".join(f"{_quoted(pk)} = ?" for pk in pks)
        for column in columns:
            if column not in info:
                continue
            qcol = _quoted(column)
            rows = execute(
                conn,
                f"SELECT {q_pks}, {qcol} FROM {qtable} WHERE typeof({qcol}) = 'text'",
            ).fetchall()
            notnull = bool(info[column][3])
            n = 0
            for row in rows:
                raw = row[-1]
                parsed = _parse_epoch(raw)
                if parsed is None:
                    parsed = 0 if notnull else None
                pk_vals = [row[i] for i in range(len(pks))]
                execute(
                    conn,
                    f"UPDATE {qtable} SET {qcol} = ? WHERE {where_pk}",
                    (parsed, *pk_vals),
                )
                n += 1
            if n:
                changed[f"{table}.{column}"] = n
    return changed


# pgloader 3.6.x TYPE-ERRORs on SQLite expression indexes (LOWER(...)).
# post_load_constraints.sql recreates these on Postgres after load.
_EXPRESSION_INDEXES_TO_DROP = (
    "idx_keyword_metrics_keyword_lower",
    "idx_keyword_page_map_keyword_lower",
    "idx_competitor_gaps_keyword_lower",
)


def drop_expression_indexes(conn: Any) -> dict[str, int]:
    """Drop known SQLite expression indexes that pgloader cannot migrate."""
    dropped: dict[str, int] = {}
    for name in _EXPRESSION_INDEXES_TO_DROP:
        row = execute(
            conn,
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,),
        ).fetchone()
        if not row:
            continue
        execute(conn, f"DROP INDEX IF EXISTS {_quoted(name)}")
        dropped[name] = 1
    return dropped


def fix_sqlite_copy(path: str | Path) -> dict[str, dict[str, int]]:
    """Apply pre-load data fixes to ``path`` (must already be a working copy)."""
    conn = connect_sqlite(path)
    try:
        empty = fix_empty_strings_in_numeric_columns(conn)
        epochs = fix_integer_epoch_columns(conn)
        expr = drop_expression_indexes(conn)
        conn.commit()
        return {"empty_numeric": empty, "epoch_text": epochs, "expression_indexes_dropped": expr}
    finally:
        conn.close()
