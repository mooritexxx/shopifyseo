"""Tests for SQLite retry utility and busy_timeout application."""
import sqlite3
from unittest.mock import patch

import pytest

from shopifyseo.sqlite_retry import (
    DB_LOCK_INITIAL_BACKOFF_MS,
    DB_LOCK_MAX_RETRIES,
    run_with_db_lock_retry,
)


def test_run_with_db_lock_retry_succeeds_first_try():
    """Verify function returns immediately on success."""
    call_count = [0]

    def success_fn():
        call_count[0] += 1
        return "result"

    result = run_with_db_lock_retry(success_fn)
    assert result == "result"
    assert call_count[0] == 1


def test_run_with_db_lock_retry_retries_on_lock():
    """Verify function retries on database locked errors."""
    call_count = [0]

    def flaky_fn():
        call_count[0] += 1
        if call_count[0] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "success"

    result = run_with_db_lock_retry(flaky_fn, max_retries=5)
    assert result == "success"
    assert call_count[0] == 3


def test_run_with_db_lock_retry_exhausts_retries():
    """Verify exception is raised after all retries are exhausted."""
    call_count = [0]

    def always_locked():
        call_count[0] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        run_with_db_lock_retry(always_locked, max_retries=3)

    assert call_count[0] == 3


def test_run_with_db_lock_retry_raises_non_lock_errors():
    """Verify non-lock errors are raised immediately."""
    call_count = [0]

    def other_error():
        call_count[0] += 1
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        run_with_db_lock_retry(other_error, max_retries=5)

    assert call_count[0] == 1  # No retries for non-lock errors


def test_run_with_db_lock_retry_uses_exponential_backoff():
    """Verify exponential backoff timing."""
    call_count = [0]
    sleep_times = []

    def always_locked():
        call_count[0] += 1
        raise sqlite3.OperationalError("database is locked")

    with patch("time.sleep", side_effect=lambda t: sleep_times.append(t)):
        with pytest.raises(sqlite3.OperationalError):
            run_with_db_lock_retry(always_locked, max_retries=4, initial_backoff_ms=100)

    # Should have 3 sleeps (after attempts 1, 2, 3; not after final attempt 4)
    assert len(sleep_times) == 3
    # Exponential backoff: 100ms, 200ms, 400ms
    assert sleep_times[0] == pytest.approx(0.1)
    assert sleep_times[1] == pytest.approx(0.2)
    assert sleep_times[2] == pytest.approx(0.4)


def test_default_retry_constants():
    """Verify default constants are set appropriately."""
    assert DB_LOCK_MAX_RETRIES == 5
    assert DB_LOCK_INITIAL_BACKOFF_MS == 100


class TestBusyTimeoutPragmaApplication:
    """Tests verifying busy_timeout is applied across connection helpers."""

    def test_backend_db_open_db_connection_sets_busy_timeout(self, tmp_path):
        """Verify backend/app/db.py open_db_connection sets busy_timeout."""
        from backend.app import db as backend_db
        from shopifyseo.dashboard_store import DB_PATH

        # Use in-memory database for test
        original_path = backend_db.DB_PATH
        test_db = str(tmp_path / "test.db")
        backend_db.DB_PATH = test_db

        try:
            # Clear bootstrapped paths to force re-bootstrap
            backend_db._bootstrapped_paths.clear()

            conn = backend_db.open_db_connection()
            try:
                result = conn.execute("PRAGMA busy_timeout").fetchone()
                assert result[0] == backend_db.BUSY_TIMEOUT_MS
            finally:
                conn.close()
        finally:
            backend_db.DB_PATH = original_path
            backend_db._bootstrapped_paths.clear()

    def test_dashboard_store_db_connect_sets_busy_timeout(self, tmp_path):
        """Verify shopifyseo/dashboard_store.py db_connect sets busy_timeout."""
        from shopifyseo import dashboard_store

        original_path = dashboard_store.DB_PATH
        test_db = str(tmp_path / "test.db")
        dashboard_store.DB_PATH = test_db

        try:
            conn = dashboard_store.db_connect()
            try:
                result = conn.execute("PRAGMA busy_timeout").fetchone()
                assert result[0] == dashboard_store.BUSY_TIMEOUT_MS
            finally:
                conn.close()
        finally:
            dashboard_store.DB_PATH = original_path

    def test_actions_state_db_connect_sets_busy_timeout(self, tmp_path):
        """Verify shopifyseo/dashboard_actions/_state.py sets busy_timeout."""
        from shopifyseo.dashboard_actions._state import (
            BUSY_TIMEOUT_MS,
            _db_connect_for_actions,
        )

        test_db = str(tmp_path / "test.db")
        conn = _db_connect_for_actions(test_db)
        try:
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == BUSY_TIMEOUT_MS
        finally:
            conn.close()

    def test_catalog_sync_open_db_sets_busy_timeout(self, tmp_path):
        """Verify shopifyseo/shopify_catalog_sync/db.py open_db sets busy_timeout."""
        from shopifyseo.shopify_catalog_sync.db import BUSY_TIMEOUT_MS, open_db

        test_db = tmp_path / "test.db"
        conn = open_db(test_db)
        try:
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == BUSY_TIMEOUT_MS
        finally:
            conn.close()
