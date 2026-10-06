"""Fail on new raw SQLite-only SQL outside shopifyseo/db/ and shopifyseo/cutover/.

Companion to test_direct_execute_allowlist.py. Remaining gated sites must stay
on this allowlist; new PRAGMA / executescript / conn.executemany / AUTOINCREMENT
in app code is a CI failure.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PARTS = {
    "tests",
    "scripts",
    ".venv",
    "node_modules",
    "__pycache__",
    ".git",
    "frontend",
    "docs",
    "shopifyseo/db",
    "shopifyseo/cutover",
}

# Remaining SQLite-shaped DDL that is skipped at runtime when DATABASE_URL is Postgres.
ALLOWED: dict[str, dict[str, int]] = {
    "backend/app/services/team_tasks.py": {
        "PRAGMA": 0,
        "executescript": 1,
        "conn.executemany": 0,
        "AUTOINCREMENT": 2,
    },
    "shopifyseo/rank_tracking/store.py": {
        "PRAGMA": 0,
        "executescript": 1,
        "conn.executemany": 0,
        "AUTOINCREMENT": 0,
    },
    "shopifyseo/shopify_catalog_sync/db.py": {
        "PRAGMA": 0,
        "executescript": 1,
        "conn.executemany": 0,
        "AUTOINCREMENT": 1,
    },
    "shopifyseo/dashboard_store.py": {
        "PRAGMA": 0,
        "executescript": 0,
        "conn.executemany": 0,
        "AUTOINCREMENT": 10,
    },
    "shopifyseo/internal_links/store.py": {
        "PRAGMA": 0,
        "executescript": 0,
        "conn.executemany": 0,
        "AUTOINCREMENT": 2,
    },
}

_PRAGMA = re.compile(r"\bPRAGMA\b")
_EXECUTESCRIPT = re.compile(r"\.executescript\s*\(")
_CONN_EXECUTEMANY = re.compile(r"\b(?:conn|c)\.executemany\s*\(")
_AUTOINCREMENT = re.compile(r"\bAUTOINCREMENT\b")


def _counts(source: str) -> dict[str, int]:
    return {
        "PRAGMA": len(_PRAGMA.findall(source)),
        "executescript": len(_EXECUTESCRIPT.findall(source)),
        "conn.executemany": len(_CONN_EXECUTEMANY.findall(source)),
        "AUTOINCREMENT": len(_AUTOINCREMENT.findall(source)),
    }


def _iter_py_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if any(p in EXCLUDED_PARTS for p in parts):
            continue
        if str(rel).startswith("shopifyseo/db/") or str(rel).startswith("shopifyseo/cutover/"):
            continue
        files.append(path)
    return files


def test_no_new_raw_sqlite_only_sql():
    violations = []
    current: dict[str, dict[str, int]] = {}
    for path in _iter_py_files():
        rel = str(path.relative_to(ROOT))
        counts = _counts(path.read_text(encoding="utf-8"))
        if not any(counts.values()):
            continue
        current[rel] = counts
        allowed = ALLOWED.get(rel, {k: 0 for k in counts})
        for key, n in counts.items():
            if n > allowed.get(key, 0):
                violations.append(f"{rel}: {key}={n} (allowed {allowed.get(key, 0)})")
    if violations:
        pytest.fail(
            "New raw SQLite-only SQL outside shopifyseo/db/ and shopifyseo/cutover/:\n  "
            + "\n  ".join(sorted(violations))
        )


def test_sqlite_only_allowlist_not_too_generous():
    over = []
    for rel, allowed in ALLOWED.items():
        path = ROOT / rel
        if not path.is_file():
            over.append(f"remove missing allowlist file {rel}")
            continue
        counts = _counts(path.read_text(encoding="utf-8"))
        for key, n in allowed.items():
            actual = counts.get(key, 0)
            if actual < n:
                over.append(f"lower {rel} {key} to {actual} (currently {n})")
    if over:
        pytest.fail("SQLite-only allowlist is too high:\n  " + "\n  ".join(sorted(over)))
