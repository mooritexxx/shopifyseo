"""Tests for shopify_catalog_sync.db.open_db function."""

import tempfile
from pathlib import Path

import pytest

from shopifyseo.shopify_catalog_sync.db import open_db


def test_open_db_accepts_string_path():
    """open_db must accept a str path without raising AttributeError.

    Regression test for the Apply 502 bug where str(DB_PATH) passed to
    sync_* → open_db caused 'str' object has no attribute 'parent'.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path_str = f"{tmpdir}/subdir/test.sqlite3"
        conn = open_db(db_path_str)
        try:
            # Verify connection works
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            assert len(tables) > 0, "Schema should be created"
        finally:
            conn.close()
        # Verify the file was created
        assert Path(db_path_str).exists()


def test_open_db_accepts_path_object():
    """open_db continues to accept Path objects as before."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "nested" / "test.sqlite3"
        conn = open_db(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            assert len(tables) > 0
        finally:
            conn.close()
        assert db_path.exists()
