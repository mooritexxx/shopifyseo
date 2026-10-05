"""SQLite → PostgreSQL cutover helpers (plan 8).

These modules are invoked by ``scripts/pg_cutover.sh`` and
``scripts/pg_to_sqlite_delta.py``. They never set live ``DATABASE_URL`` and
never start uvicorn.
"""

from .catalog import (
    DELTA_TABLES,
    FOREIGN_KEYS,
    INTEGER_EPOCH_COLUMNS,
    SKIP_IDENTITY_TABLES,
    TIMESTAMP_COLUMNS,
    list_user_tables,
)
from .delta import apply_delta_to_sqlite_copy, export_pg_delta, refuse_live_sqlite
from .sequences import resync_cutover_identities
from .sqlite_pre_fix import fix_sqlite_copy
from .verify import verify_counts, verify_values

__all__ = [
    "DELTA_TABLES",
    "FOREIGN_KEYS",
    "INTEGER_EPOCH_COLUMNS",
    "SKIP_IDENTITY_TABLES",
    "TIMESTAMP_COLUMNS",
    "apply_delta_to_sqlite_copy",
    "export_pg_delta",
    "fix_sqlite_copy",
    "list_user_tables",
    "refuse_live_sqlite",
    "resync_cutover_identities",
    "verify_counts",
    "verify_values",
]
