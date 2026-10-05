"""Database abstraction layer for SQLite/PostgreSQL portability.

Backend is selected via DATABASE_URL environment variable:
- Unset/empty: SQLite (default path)
- postgresql:// or postgres://: PostgreSQL via psycopg
- sqlite:///path or file:path or bare path: SQLite
"""
from .backend import Backend, InvalidDatabaseURL, get_backend, is_postgres, is_sqlite, parse_database_url
from .compat import DictRow, translate_placeholders
from .connect import BUSY_TIMEOUT_MS, connect, connect_postgres, connect_sqlite
from .exceptions import DatabaseError, IntegrityError, LockError, OperationalError, ProgrammingError
from .helpers import insert_returning_id, table_columns, table_exists, write_tx

__all__ = [
    "Backend",
    "InvalidDatabaseURL",
    "get_backend",
    "is_postgres",
    "is_sqlite",
    "parse_database_url",
    "connect",
    "connect_sqlite",
    "connect_postgres",
    "BUSY_TIMEOUT_MS",
    "DictRow",
    "translate_placeholders",
    "insert_returning_id",
    "write_tx",
    "table_exists",
    "table_columns",
    "DatabaseError",
    "IntegrityError",
    "OperationalError",
    "LockError",
    "ProgrammingError",
]
