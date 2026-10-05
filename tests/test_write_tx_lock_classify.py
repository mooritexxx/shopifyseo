"""Regression tests for write_tx rollback and lock-error classification."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from shopifyseo.db import (
    Backend,
    connect_sqlite,
    is_lock_error,
    map_exception,
    write_tx,
)


def test_write_tx_preserves_original_error_when_sqlite_already_rolled_back():
    """RAISE(ROLLBACK) ends the tx; caller must see IntegrityError('rb'), not rollback fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite3"
        conn = connect_sqlite(db_path, wal_mode=False)
        conn.isolation_level = None
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute(
                """
                CREATE TRIGGER t_rb BEFORE INSERT ON t
                BEGIN
                    SELECT RAISE(ROLLBACK, 'rb');
                END
                """
            )
            with pytest.raises(sqlite3.IntegrityError, match=r"^rb$") as caught:
                with write_tx(conn, backend=Backend.SQLITE):
                    conn.execute("INSERT INTO t VALUES (1)")
            assert "cannot rollback" not in str(caught.value).lower()
            assert not conn.in_transaction
            assert conn.execute("SELECT * FROM t").fetchone() is None
        finally:
            conn.close()


def test_wrapped_lock_is_not_a_lock_error():
    """Do not walk __cause__: RuntimeError from a lock is not itself a lock."""
    try:
        raise sqlite3.OperationalError("database is locked")
    except sqlite3.OperationalError as locked:
        err = RuntimeError("wrapped")
        err.__cause__ = locked
    assert not is_lock_error(err)
    assert is_lock_error(err.__cause__)


def test_foreign_lockerror_name_is_not_a_lock_error():
    """A non-psycopg class named LockError is not treated as a lock."""

    class LockError(Exception):
        pass

    err = LockError("redis lock")
    assert not is_lock_error(err)
    assert map_exception(err) is err


def test_builtin_syntax_error_is_not_programming_error():
    err = SyntaxError("not sql")
    assert map_exception(err) is err
