"""Retry utilities for transient database lock errors.

Module path stays ``shopifyseo.sqlite_retry`` so the three existing callers
do not move. Behaviour is backend-agnostic: SQLite ``database is locked``
(unchanged predicate) plus mapped ``LockError`` and PostgreSQL SQLSTATEs
40001 / 40P01 / 55P03.
"""
import logging
import time
from collections.abc import Callable
from typing import TypeVar

from .db.exceptions import is_lock_error

_LOG = logging.getLogger(__name__)

# Default retry parameters.
# Total retry window should exceed busy_timeout (5000ms) to handle cases where
# concurrent writes are waiting on slow operations (e.g. embedding API calls).
# With max_retries=8 and initial_backoff=200ms, total wait time is:
# 200 + 400 + 800 + 1600 + 3200 + 6400 + 12800 = 25400ms (~25s)
# This ensures we outlast typical busy_timeout windows with margin.
DB_LOCK_MAX_RETRIES = 8
DB_LOCK_INITIAL_BACKOFF_MS = 200

T = TypeVar("T")


def run_with_db_lock_retry(
    fn: Callable[[], T],
    *,
    max_retries: int = DB_LOCK_MAX_RETRIES,
    initial_backoff_ms: int = DB_LOCK_INITIAL_BACKOFF_MS,
) -> T:
    """Run fn(), retrying on transient lock errors with exponential backoff.

    Retries when ``is_lock_error`` is true: SQLite ``database is locked``,
    ``shopifyseo.db.LockError``, and PostgreSQL SQLSTATEs 40001, 40P01, 55P03.

    Args:
        fn: The callable to execute.
        max_retries: Maximum number of attempts before re-raising.
        initial_backoff_ms: Initial backoff delay in milliseconds.

    Returns:
        The return value of fn() on success.

    Raises:
        The last exception if all retries are exhausted, or immediately for
        a non-lock error.
    """
    backoff_ms = initial_backoff_ms
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if is_lock_error(e) and attempt < max_retries - 1:
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
