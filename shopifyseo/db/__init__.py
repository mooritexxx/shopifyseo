"""Database abstraction layer for SQLite/PostgreSQL portability.

This module provides a unified interface for database operations across
SQLite and PostgreSQL backends. The backend is selected based on the
DATABASE_URL environment variable.

Public API:
-----------
Connection:
    connect(url=None, ...)     - Open connection based on DATABASE_URL
    connect_sqlite(path=None)  - Open SQLite connection explicitly
    connect_postgres(url)      - Open PostgreSQL connection explicitly

Backend Detection:
    Backend                    - Enum: SQLITE, POSTGRES
    get_backend()              - Get current backend from DATABASE_URL
    is_sqlite(), is_postgres() - Convenience checks
    parse_database_url(url)    - Parse URL to (Backend, connection_string)

Row Compatibility:
    DictRow                    - sqlite3.Row-compatible row wrapper

SQL Translation:
    translate_placeholders(sql, to_postgres=True)
                               - Convert ? to %s, respecting string literals

Helpers:
    insert_returning_id(conn, sql, params) - Portable INSERT with ID return
    write_tx(conn)             - Write transaction context manager
    table_exists(conn, table)  - Check if table exists
    table_columns(conn, table) - Get column names for table

Exceptions:
    DatabaseError              - Base class
    IntegrityError             - Constraint violations
    OperationalError           - Connection/lock/syntax errors
    LockError                  - Database locked (subclass of OperationalError)
    ProgrammingError           - Invalid SQL / param errors
    map_exception(exc, backend) - Convert backend exception to common type

Usage Example:
--------------
    from shopifyseo.db import connect, translate_placeholders, insert_returning_id

    conn = connect()  # Uses DATABASE_URL or defaults to SQLite
    sql = translate_placeholders("INSERT INTO users (name) VALUES (?)")
    row_id = insert_returning_id(conn, sql, ("Alice",))
"""
from .backend import Backend, get_backend, is_postgres, is_sqlite, parse_database_url
from .compat import DictRow, translate_placeholders
from .connect import (
    BUSY_TIMEOUT_MS,
    connect,
    connect_postgres,
    connect_sqlite,
    get_backend_for_connection,
)
from .exceptions import (
    DatabaseError,
    IntegrityError,
    LockError,
    OperationalError,
    ProgrammingError,
    map_exception,
    map_psycopg_exception,
    map_sqlite_exception,
)
from .helpers import insert_returning_id, table_columns, table_exists, write_tx

__all__ = [
    # Backend detection
    "Backend",
    "get_backend",
    "is_postgres",
    "is_sqlite",
    "parse_database_url",
    # Connection
    "connect",
    "connect_sqlite",
    "connect_postgres",
    "get_backend_for_connection",
    "BUSY_TIMEOUT_MS",
    # Row compatibility
    "DictRow",
    # SQL translation
    "translate_placeholders",
    # Helpers
    "insert_returning_id",
    "write_tx",
    "table_exists",
    "table_columns",
    # Exceptions
    "DatabaseError",
    "IntegrityError",
    "OperationalError",
    "LockError",
    "ProgrammingError",
    "map_exception",
    "map_sqlite_exception",
    "map_psycopg_exception",
]
