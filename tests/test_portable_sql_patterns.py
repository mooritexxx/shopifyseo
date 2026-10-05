"""Fail if SQLite-only SQL patterns reappear outside shopifyseo.db and tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent

EXCLUDED_PARTS = {
    "tests",
    "scripts",
    ".venv",
    "node_modules",
    "__pycache__",
    ".git",
    "frontend",
    "docs",
}

# shopifyseo/db/ may keep dialect implementations and comments.
ALLOWED_PREFIXES = ("shopifyseo/db/",)

PATTERNS = {
    "INSERT OR IGNORE": re.compile(r"INSERT\s+OR\s+IGNORE", re.IGNORECASE),
    "INSERT OR REPLACE": re.compile(r"INSERT\s+OR\s+REPLACE", re.IGNORECASE),
    "datetime('now')": re.compile(r"datetime\s*\(\s*'now'\s*\)", re.IGNORECASE),
    "GROUP_CONCAT": re.compile(r"\bGROUP_CONCAT\b"),
    "GLOB": re.compile(r"\bGLOB\b"),
    "rowid": re.compile(r"\browid\b", re.IGNORECASE),
    "changes()": re.compile(r"SELECT\s+changes\s*\(", re.IGNORECASE),
    "lastrowid": re.compile(r"\blastrowid\b"),
    "last_insert_rowid": re.compile(r"\blast_insert_rowid\b"),
    "COLLATE NOCASE": re.compile(r"COLLATE\s+NOCASE", re.IGNORECASE),
}


def _iter_production_py() -> list[Path]:
    files = []
    for py_file in WORKSPACE.rglob("*.py"):
        rel = py_file.relative_to(WORKSPACE)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        rel_s = str(rel)
        if rel_s.startswith(ALLOWED_PREFIXES):
            continue
        files.append(py_file)
    return files


def test_sqlite_only_sql_cleared_outside_db_layer():
    violations: list[str] = []
    for path in _iter_production_py():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(WORKSPACE)
        for name, regex in PATTERNS.items():
            for match in regex.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                violations.append(f"{rel}:{line_no}: {name}")
    if violations:
        listed = "\n  ".join(violations)
        pytest.fail(
            "SQLite-only SQL remains outside shopifyseo/db/ (and tests):\n  "
            + listed
        )
