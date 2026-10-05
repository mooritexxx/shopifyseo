"""Database abstraction layer for SQLite/PostgreSQL portability.

Backend is selected via DATABASE_URL environment variable:
- Unset/empty: SQLite (default path)
- postgresql:// or postgres://: PostgreSQL via psycopg
- sqlite:///path or file:path or bare path: SQLite
"""
from .backend import Backend, InvalidDatabaseURL, get_backend, is_postgres, is_sqlite, parse_database_url
from .compat import DictRow
from .connect import BUSY_TIMEOUT_MS, connect, connect_postgres, connect_sqlite
from .exceptions import (
    DatabaseError,
    IntegrityError,
    LockError,
    OperationalError,
    ProgrammingError,
    is_integrity_error,
    is_lock_error,
    is_operational_error,
    map_exception,
)
from .execute import execute, executemany, get_connection
from .helpers import (
    busy_timeout,
    foreign_keys_enabled,
    group_concat,
    index_exists,
    insert_returning_id,
    journal_mode,
    like_ci,
    LOCK_RANK_JOBS,
    LOCK_TEAM_TASKS,
    on_conflict_do_nothing,
    on_conflict_do_update,
    order_ci,
    order_inserted,
    set_foreign_keys,
    table_columns,
    table_ddl,
    table_exists,
    write_tx,
)
from .identity import (
    IDENTITY_COLUMNS,
    ResyncResult,
    create_identity_column_ddl,
    ensure_identity,
    get_sequence_name,
    identity_ddl,
    resync_all_sequences,
    resync_sequence,
    serial_ddl,
)

__all__ = [
    # Backend detection
    "Backend",
    "InvalidDatabaseURL",
    "get_backend",
    "is_postgres",
    "is_sqlite",
    "parse_database_url",
    # Connection
    "connect",
    "connect_sqlite",
    "connect_postgres",
    "get_connection",
    "BUSY_TIMEOUT_MS",
    # Execute wrapper
    "execute",
    "executemany",
    # Row compatibility
    "DictRow",
    # Helpers
    "insert_returning_id",
    "LOCK_RANK_JOBS",
    "LOCK_TEAM_TASKS",
    "group_concat",
    "like_ci",
    "order_ci",
    "order_inserted",
    "on_conflict_do_nothing",
    "on_conflict_do_update",
    "write_tx",
    "table_exists",
    "table_columns",
    "table_ddl",
    "index_exists",
    "foreign_keys_enabled",
    "set_foreign_keys",
    "journal_mode",
    "busy_timeout",
    # Identity columns
    "IDENTITY_COLUMNS",
    "ResyncResult",
    "identity_ddl",
    "serial_ddl",
    "get_sequence_name",
    "resync_sequence",
    "resync_all_sequences",
    "ensure_identity",
    "create_identity_column_ddl",
    # Exceptions
    "DatabaseError",
    "IntegrityError",
    "OperationalError",
    "LockError",
    "ProgrammingError",
    "map_exception",
    "is_integrity_error",
    "is_lock_error",
    "is_operational_error",
]
