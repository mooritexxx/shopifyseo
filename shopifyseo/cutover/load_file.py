"""Parse-check ``scripts/pg_cutover/shopifyseo.load``.

pgloader 3.6.10 rejects an unquoted multi-word CAST target
(``type real to double precision`` → ESRAP-PARSE-ERROR). The working form is
``type real to "double precision" drop typemod using float-to-string``.
These helpers never open a catalog or invoke pgloader.
"""

from __future__ import annotations

import re
from pathlib import Path

from .real_columns import PGLOADER_FLOAT_SOURCE_TYPES
from .sql import CUTOVER_SQL_DIR

LOAD_FILE = CUTOVER_SQL_DIR / "shopifyseo.load"

# Target must be quoted. Unquoted ``to double precision`` does not parse.
_FLOAT_CAST = re.compile(
    r'^type\s+(?P<src>real|float|double|"double precision")\s+'
    r'to\s+"double precision"\s+drop typemod\s+using float-to-string$',
    re.IGNORECASE,
)
_UNQUOTED_TARGET = re.compile(r"\bto double precision\b", re.IGNORECASE)


def cast_rules(load_text: str) -> list[str]:
    """Return CAST clause lines, skipping comments that mention CAST."""
    lines = load_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("CAST "):
            start = i
            break
    if start is None:
        raise ValueError("shopifyseo.load must contain a CAST block")
    block: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            break
        if line.lstrip().startswith("--"):
            continue
        block.append(line.strip().rstrip(","))
    return block


def validate_load_file(path: Path | None = None) -> list[str]:
    """Require quoted ``\"double precision\"`` targets for every float CAST.

    Raises ``ValueError`` on the unquoted form that pgloader 3.6.10 rejects.
    """
    load_path = path or LOAD_FILE
    text = load_path.read_text(encoding="utf-8")
    rules = cast_rules(text)
    found: dict[str, str] = {}
    for line in rules:
        if _UNQUOTED_TARGET.search(line):
            raise ValueError(
                "pgloader 3.6.10 rejects an unquoted CAST target "
                f"(ESRAP-PARSE-ERROR): {line!r}. Use "
                'type … to "double precision" drop typemod using float-to-string'
            )
        match = _FLOAT_CAST.match(line)
        if match:
            src = match.group("src").strip('"').lower()
            found[src] = line
    missing = [src for src in PGLOADER_FLOAT_SOURCE_TYPES if src not in found]
    if missing:
        raise ValueError(
            "shopifyseo.load is missing quoted double-precision CAST rules for "
            f"{missing}. Have: {rules}"
        )
    return rules
