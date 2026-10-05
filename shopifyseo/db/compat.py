"""Row compatibility and placeholder translation for database portability.

Provides:
- DictRow: A row type compatible with sqlite3.Row (index, key access, dict(), .keys())
- translate_placeholders: Safe ? -> %s conversion that respects string literals
"""
from __future__ import annotations

import re
from typing import Any, Iterator


class DictRow:
    """Row wrapper compatible with sqlite3.Row interface.

    Supports:
    - Index access: row[0], row[1]
    - Key access: row["column_name"]
    - dict(row) conversion
    - row.keys() iteration
    - len(row)
    """

    __slots__ = ("_data", "_keys")

    def __init__(self, data: dict[str, Any] | None = None, keys: tuple[str, ...] | list[str] | None = None):
        if data is None:
            data = {}
        self._data = data
        self._keys = tuple(keys) if keys is not None else tuple(data.keys())

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            if key < 0:
                key = len(self._keys) + key
            if 0 <= key < len(self._keys):
                return self._data[self._keys[key]]
            raise IndexError(f"index {key} out of range")
        return self._data[key]

    def __iter__(self) -> Iterator[Any]:
        return (self._data[k] for k in self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def keys(self) -> tuple[str, ...]:
        return self._keys

    def items(self) -> Iterator[tuple[str, Any]]:
        return ((k, self._data[k]) for k in self._keys)

    def values(self) -> Iterator[Any]:
        return (self._data[k] for k in self._keys)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __repr__(self) -> str:
        return f"DictRow({self._data!r})"


_PLACEHOLDER_PATTERN = re.compile(
    r"""
    '(?:[^'\\]|\\.)*'           # single-quoted string (handles escapes)
    |
    "(?:[^"\\]|\\.)*"           # double-quoted string (handles escapes)
    |
    \?\?                        # escaped placeholder (?? -> ? in PostgreSQL)
    |
    \?                          # single placeholder
    """,
    re.VERBOSE,
)


def translate_placeholders(sql: str, to_postgres: bool = True) -> str:
    """Translate ? placeholders to %s for PostgreSQL, preserving string literals.

    - Single ? outside strings -> %s
    - ?? (escaped) -> single ? (PostgreSQL convention, though rarely used)
    - ? inside quoted strings is left alone

    For SQLite (to_postgres=False), returns the SQL unchanged.
    """
    if not to_postgres:
        return sql

    def replacer(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.startswith("'") or text.startswith('"'):
            return text
        if text == "??":
            return "?"
        if text == "?":
            return "%s"
        return text

    return _PLACEHOLDER_PATTERN.sub(replacer, sql)


def row_factory_for_cursor(cursor: Any) -> DictRow | None:
    """Create a DictRow from the current cursor row description.

    For use as a psycopg row_factory that mimics sqlite3.Row.
    """
    if cursor.description is None:
        return None
    columns = tuple(col.name for col in cursor.description)

    def make_row(values: tuple[Any, ...]) -> DictRow:
        return DictRow(dict(zip(columns, values)), columns)

    return make_row  # type: ignore[return-value]
