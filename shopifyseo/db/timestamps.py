"""UTC naive-text timestamp parity for SQLite and PostgreSQL.

SQLite ``CURRENT_TIMESTAMP`` stores ``YYYY-MM-DD HH:MM:SS`` in UTC with no
offset and no fractional seconds. PostgreSQL ``CURRENT_TIMESTAMP`` instead
writes a timestamptz-style value (offset, often fractional seconds, session
timezone). This module:

- Exposes ``now_text()`` / ``now_text_sql()`` for Python and SQL call sites
- Rewrites ``CURRENT_TIMESTAMP`` to ``to_char(now() AT TIME ZONE 'utc', …)``
  on Postgres (execute wrapper + connection ``execute`` / cursor, so the
  remaining ``conn.execute`` sites are covered without editing ~45 SQL strings)
- Leaves SQLite SQL unchanged (native ``CURRENT_TIMESTAMP`` is already UTC text)

INTEGER epoch columns (e.g. ``keyword_metrics.updated_at``) must use
``now_epoch()`` / ``as_epoch_seconds()``, never ``CURRENT_TIMESTAMP``.
"""
from __future__ import annotations

import calendar
import re
import time
from datetime import datetime, timezone
from typing import Any

from .backend import Backend, get_backend

NOW_TEXT_FORMAT = "%Y-%m-%d %H:%M:%S"
NOW_TEXT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# Postgres TEXT stand-in for SQLite CURRENT_TIMESTAMP. No '%' so percent-escaping
# in the placeholder translator is a no-op on this fragment.
PG_NOW_TEXT_SQL = "to_char((now() AT TIME ZONE 'utc'), 'YYYY-MM-DD HH24:MI:SS')"

_CURRENT_TIMESTAMP_RE = re.compile(r"\bCURRENT_TIMESTAMP\b", re.IGNORECASE)

# Naive SQLite CURRENT_TIMESTAMP, optional fraction, optional Z / ±HH:MM offset.
_EPOCH_TEXT_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):?(\d{2}))?$"
)

_PG_TIMEZONE_OPTIONS = "-c timezone=UTC"


def now_text(dt: datetime | None = None) -> str:
    """Return UTC ``YYYY-MM-DD HH:MM:SS`` with no offset or fractional seconds."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(NOW_TEXT_FORMAT)


def now_epoch() -> int:
    """UTC unix seconds for INTEGER epoch timestamp columns."""
    return int(time.time())


def as_epoch_seconds(value: Any | None = None) -> int:
    """Coerce a value to unix seconds (UTC).

    ``None`` → now. Integers/floats are truncated. Digit strings parse as
    epoch. ``YYYY-MM-DD HH:MM:SS`` (the SQLite CURRENT_TIMESTAMP shape, with
    optional fraction / offset) is read as UTC so a TEXT timestamp cannot be
    written into an INTEGER column.
    """
    if value is None:
        return now_epoch()
    if isinstance(value, bool):
        raise TypeError("as_epoch_seconds does not accept bool")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    text = str(value).strip()
    if not text:
        return now_epoch()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    match = _EPOCH_TEXT_RE.match(text)
    if not match:
        raise ValueError(f"Cannot coerce {value!r} to epoch seconds")
    year, month, day, hour, minute, second, sign, off_h, off_m = match.groups()
    struct = (
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second),
        0,
        0,
        0,
    )
    epoch = calendar.timegm(struct)
    if sign and off_h is not None:
        offset = int(off_h) * 3600 + int(off_m or 0) * 60
        epoch -= offset if sign == "+" else -offset
    return epoch


def now_text_sql(*, backend: Backend | None = None) -> str:
    """SQL expression that yields SQLite-shaped UTC text on both backends."""
    resolved = get_backend() if backend is None else backend
    if resolved == Backend.POSTGRES:
        return PG_NOW_TEXT_SQL
    return "CURRENT_TIMESTAMP"


def nullif_empty(expr: str) -> str:
    """SQL fragment ``NULLIF(expr, '')`` so empty date strings cast as NULL.

    Portable: SQLite and Postgres both have ``NULLIF``.
    """
    return f"NULLIF({expr}, '')"


def empty_to_null(value: Any) -> str | None:
    """Python stand-in for ``NULLIF(col, '')`` on writers."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def rewrite_current_timestamp_for_postgres(sql: str) -> str:
    """Replace ``CURRENT_TIMESTAMP`` tokens with ``PG_NOW_TEXT_SQL``.

    String literals, quoted identifiers, and comments are left unchanged.
    Idempotent: a second pass finds no ``CURRENT_TIMESTAMP``.
    """
    if not sql or "CURRENT_TIMESTAMP" not in sql.upper():
        return sql
    from .compat import _TOKEN_PATTERN

    parts: list[str] = []
    for match in _TOKEN_PATTERN.finditer(sql):
        token = match.group(0)
        if (
            token.startswith("'")
            or token.startswith('"')
            or token.startswith("--")
            or token.startswith("/*")
        ):
            parts.append(token)
        else:
            parts.append(_CURRENT_TIMESTAMP_RE.sub(PG_NOW_TEXT_SQL, token))
    return "".join(parts)


def postgres_connect_options(existing_options: str | None = None) -> str:
    """libpq ``options`` value that sets session ``timezone=UTC``.

    Merges with any existing ``options`` so a URL that already passes
    ``-c`` flags is not overwritten. Used only on the Postgres connect path.
    """
    existing = (existing_options or "").strip()
    if "timezone=" in existing.lower():
        return existing
    if not existing:
        return _PG_TIMEZONE_OPTIONS
    return f"{existing} {_PG_TIMEZONE_OPTIONS}"


def attach_postgres_timestamp_parity(conn: Any) -> Any:
    """Patch ``execute`` / ``cursor()`` so CURRENT_TIMESTAMP is rewritten.

    Direct ``conn.execute`` call sites (still the majority) never go through
    ``shopifyseo.db.execute``; wrapping the connection covers them. The
    connection stays a ``psycopg.Connection`` (tests assert isinstance).
    """
    if getattr(conn, "_shopifyseo_ts_parity", False):
        return conn

    orig_execute = conn.execute
    orig_cursor = conn.cursor

    def execute(query: Any, params: Any = None, **kwargs: Any) -> Any:
        if isinstance(query, str):
            query = rewrite_current_timestamp_for_postgres(query)
        if params is None and not kwargs:
            return orig_execute(query)
        return orig_execute(query, params, **kwargs)

    def cursor(*args: Any, **kwargs: Any) -> Any:
        cur = orig_cursor(*args, **kwargs)
        _patch_postgres_cursor(cur)
        return cur

    conn.execute = execute
    conn.cursor = cursor
    conn._shopifyseo_ts_parity = True
    return conn


def _patch_postgres_cursor(cur: Any) -> Any:
    orig_execute = cur.execute
    orig_executemany = cur.executemany

    def execute(query: Any, params: Any = None, **kwargs: Any) -> Any:
        if isinstance(query, str):
            query = rewrite_current_timestamp_for_postgres(query)
        if params is None and not kwargs:
            return orig_execute(query)
        return orig_execute(query, params, **kwargs)

    def executemany(query: Any, params_seq: Any, **kwargs: Any) -> Any:
        if isinstance(query, str):
            query = rewrite_current_timestamp_for_postgres(query)
        return orig_executemany(query, params_seq, **kwargs)

    cur.execute = execute
    cur.executemany = executemany
    return cur
