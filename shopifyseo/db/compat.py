"""Row compatibility and placeholder translation for database portability."""
from __future__ import annotations

import re
from typing import Any, Iterator


class DictRow:
    """Row wrapper compatible with sqlite3.Row interface.

    Supports index access, case-insensitive key access, dict(), keys(), len().
    Handles duplicate column names like sqlite3.Row: values are stored positionally,
    and key lookup returns the first match.
    """

    __slots__ = ("_values", "_keys", "_key_to_index")

    def __init__(self, data: dict[str, Any] | None = None, keys: tuple[str, ...] | list[str] | None = None):
        if data is None:
            data = {}
        self._keys = tuple(keys) if keys is not None else tuple(data.keys())
        self._values = tuple(data.get(k) for k in self._keys) if data else ()
        self._key_to_index: dict[str, int] = {}
        for i, k in enumerate(self._keys):
            lower_k = k.lower()
            if lower_k not in self._key_to_index:
                self._key_to_index[lower_k] = i

    @classmethod
    def from_values(cls, keys: tuple[str, ...], values: tuple[Any, ...]) -> "DictRow":
        """Create a DictRow from separate keys and values tuples."""
        row = cls.__new__(cls)
        row._keys = keys
        row._values = values
        row._key_to_index = {}
        for i, k in enumerate(keys):
            lower_k = k.lower()
            if lower_k not in row._key_to_index:
                row._key_to_index[lower_k] = i
        return row

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            if key < 0:
                key = len(self._keys) + key
            if 0 <= key < len(self._values):
                return self._values[key]
            raise IndexError(f"index {key} out of range")
        idx = self._key_to_index.get(key.lower())
        if idx is None:
            raise KeyError(key)
        return self._values[idx]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._key_to_index

    def keys(self) -> tuple[str, ...]:
        return self._keys

    def items(self) -> Iterator[tuple[str, Any]]:
        return zip(self._keys, self._values)

    def values(self) -> Iterator[Any]:
        return iter(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        idx = self._key_to_index.get(key.lower())
        if idx is None:
            return default
        return self._values[idx]


_TOKEN_PATTERN = re.compile(
    r"""
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
    [^'"\-/?]+                      # other text (no special chars)
    |
    .                               # single char fallback
    """,
    re.VERBOSE,
)


# Word-boundary LIKE only: does not match ILIKE, likes, unlike_count.
_LIKE_KEYWORD = re.compile(r"(?<![A-Za-z0-9_])LIKE(?![A-Za-z0-9_])", re.IGNORECASE)


def _is_protected_sql_token(token: str) -> bool:
    """True for string literals, quoted identifiers, and comments."""
    if not token:
        return False
    if token[0] in "'\"":
        return True
    return token.startswith("--") or token.startswith("/*")


def _rewrite_like_to_ilike(token: str) -> str:
    """Rewrite SQL ``LIKE`` / ``NOT LIKE`` to ``ILIKE`` / ``NOT ILIKE``.

    Leaves ``ILIKE``, column names (``likes``, ``unlike_count``), string
    literals, quoted identifiers, and comments unchanged. ``ESCAPE`` clauses
    are preserved because only the operator keyword is rewritten.
    """
    if _is_protected_sql_token(token):
        return token

    def _sub(match: re.Match[str]) -> str:
        word = match.group(0)
        prefix = "I" if word[0].isupper() else "i"
        return f"{prefix}{word}"

    return _LIKE_KEYWORD.sub(_sub, token)


def _translate_placeholders(sql: str, to_postgres: bool = True, *, escape_percent: bool | None = None) -> str:
    """Translate ? placeholders to %s and LIKE to ILIKE for PostgreSQL.

    - ? outside strings/comments -> %s
    - ?? -> ? (jsonb operator escape)
    - ? inside strings/identifiers/comments preserved
    - LIKE / NOT LIKE -> ILIKE / NOT ILIKE (SQLite LIKE is ASCII CI; PG LIKE is not)
    - like inside '...' / \"...\" / comments is not rewritten
    - already-ILIKE, likes, unlike_count are not rewritten
    - ESCAPE clauses are left in place

    When ``to_postgres`` is False the SQL is returned unchanged (SQLite path).

    Args:
        sql: The SQL string to translate.
        to_postgres: If False, return sql unchanged.
        escape_percent: If True, escape % to %% for psycopg. If None (default),
            auto-detect: escape only if there are ? placeholders being translated.
            Set to True when params will be passed to execute (even empty () or []),
            since psycopg processes % whenever any params sequence is passed.
    """
    if not to_postgres:
        return sql

    has_placeholders = False
    tokens = []
    for match in _TOKEN_PATTERN.finditer(sql):
        token = match.group(0)
        if token == "?":
            has_placeholders = True
            tokens.append("%s")
        elif token == "??":
            tokens.append("?")
        else:
            tokens.append(_rewrite_like_to_ilike(token))

    should_escape = escape_percent if escape_percent is not None else has_placeholders
    if should_escape:
        result = []
        for token in tokens:
            if token == "%s":
                result.append(token)
            else:
                result.append(token.replace("%", "%%"))
        return "".join(result)
    return "".join(tokens)
