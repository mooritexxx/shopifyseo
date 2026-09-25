"""SQLite retry utilities for handling transient lock errors.

Provides retry-with-backoff logic for database operations that may encounter
"database is locked" errors during concurrent writes.
"""
import logging
import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

_LOG = logging.getLogger(__name__)

# Default retry parameters
DB_LOCK_MAX_RETRIES = 5
DB_LOCK_INITIAL_BACKOFF_MS = 100

T = TypeVar("T")


def run_with_db_lock_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = DB_LOCK_MAX_RETRIES,
    initial_backoff_ms: int = DB_LOCK_INITIAL_BACKOFF_MS,
) -> T:
    """Run fn(), retrying on SQLite 'database is locked' errors with exponential backoff.

    Args:
        fn: The callable to execute.
        max_retries: Maximum number of attempts before re-raising.
        initial_backoff_ms: Initial backoff delay in milliseconds.

    Returns:
        The return value of fn() on success.

    Raises:
        sqlite3.OperationalError: If all retries are exhausted or a non-lock error occurs.
    """
    backoff_ms = initial_backoff_ms
    for attempt in range(max_retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                _LOG.warning(
                    "Database locked on attempt %d/%d, retrying in %dms",
                    attempt + 1,
                    max_retries,
                    backoff_ms,
                )
                time.sleep(backoff_ms / 1000.0)
                backoff_ms *= 2
            else:
                raise
    raise AssertionError("unreachable")  # pragma: no cover
