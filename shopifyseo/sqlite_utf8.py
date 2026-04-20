"""SQLite TEXT decoding after damaged or ``.recover``-rebuilt databases.

Invalid UTF-8 in TEXT cells makes the default sqlite3 decoder raise
``OperationalError: Could not decode to UTF-8 column ...``.  Using a custom
``text_factory`` decodes with replacement so the app stays up and sync/API
paths can overwrite bad values.
"""

from __future__ import annotations

import sqlite3


def utf8_text_factory(data: bytes | bytearray | memoryview) -> str:
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    return data.decode("utf-8", errors="replace")


def configure_sqlite_text_decode(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.text_factory = utf8_text_factory  # type: ignore[assignment]
    return conn
