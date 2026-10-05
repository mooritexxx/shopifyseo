"""Row compatibility and placeholder translation for database portability."""
from __future__ import annotations

import re
from typing import Any, Iterator


class DictRow:
    """Row wrapper compatible with sqlite3.Row interface.

    Supports index access, case-insensitive key access, dict(), keys(), len().
    """

    __slots__ = ("_data", "_keys", "_lower_map")

    def __init__(self, data: dict[str, Any] | None = None, keys: tuple[str, ...] | list[str] | None = None):
        if data is None:
            data = {}
        self._data = data
        self._keys = tuple(keys) if keys is not None else tuple(data.keys())
        self._lower_map = {k.lower(): k for k in self._keys}

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            if key < 0:
                key = len(self._keys) + key
            if 0 <= key < len(self._keys):
                return self._data[self._keys[key]]
            raise IndexError(f"index {key} out of range")
        real_key = self._lower_map.get(key.lower())
        if real_key is None:
            raise KeyError(key)
        return self._data[real_key]

    def __iter__(self) -> Iterator[Any]:
        return (self._data[k] for k in self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._lower_map

    def keys(self) -> tuple[str, ...]:
        return self._keys

    def items(self) -> Iterator[tuple[str, Any]]:
        return ((k, self._data[k]) for k in self._keys)

    def values(self) -> Iterator[Any]:
        return (self._data[k] for k in self._keys)

    def get(self, key: str, default: Any = None) -> Any:
        real_key = self._lower_map.get(key.lower())
        if real_key is None:
            return default
        return self._data.get(real_key, default)


_TOKEN_PATTERN = re.compile(
    r"""
    E'(?:[^'\\]|\\.)*'              # E'...' string with backslash escapes
    |
    '(?:[^']|'')*'                  # standard '...' string ('' is escape, NO backslash)
    |
    "(?:[^"]|"")*"                  # "..." identifier
    |
    --[^\n]*                        # -- single-line comment
    |
    /\*[\s\S]*?\*/                  # /* */ multi-line comment
    |
    \?\?                            # ?? -> ? (escaped placeholder / jsonb)
    |
    \?                              # ? placeholder
    |
    [^E'"\-/?]+                     # other text (no special chars)
    |
    .                               # single char fallback
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _escape_percent(s: str) -> str:
    """Escape % to %% for psycopg (except inside ?? which becomes ?)."""
    return s.replace("%", "%%")


def translate_placeholders(sql: str, to_postgres: bool = True) -> str:
    """Translate ? placeholders to %s for PostgreSQL.

    - ? outside strings/comments -> %s
    - ?? -> ? (jsonb operator escape)
    - % anywhere -> %% (psycopg requires this for ALL % except placeholders)
    - ? inside strings/identifiers/comments preserved
    """
    if not to_postgres:
        return sql

    result = []
    for match in _TOKEN_PATTERN.finditer(sql):
        token = match.group(0)
        if token.startswith(("'", '"', "E'", "e'")):
            result.append(_escape_percent(token))
        elif token.startswith(("--", "/*")):
            result.append(_escape_percent(token))
        elif token == "??":
            result.append("?")
        elif token == "?":
            result.append("%s")
        else:
            result.append(_escape_percent(token))
    return "".join(result)
