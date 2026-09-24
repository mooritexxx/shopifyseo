"""Test _row_get helper handles sqlite3.Row and dict uniformly."""

import sqlite3

from shopifyseo.dashboard_ai_engine_parts.context import _row_get


def _make_sqlite_row(data: dict) -> sqlite3.Row:
    """Create a sqlite3.Row from a dict for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    columns = ", ".join(data.keys())
    placeholders = ", ".join("?" * len(data))
    conn.execute(f"CREATE TABLE t ({columns})")
    conn.execute(f"INSERT INTO t VALUES ({placeholders})", tuple(data.values()))
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


def test_row_get_with_dict():
    d = {"handle": "foo", "title": "Foo"}
    assert _row_get(d, "handle") == "foo"
    assert _row_get(d, "title") == "Foo"
    assert _row_get(d, "missing") is None
    assert _row_get(d, "missing", "default") == "default"


def test_row_get_with_sqlite_row():
    row = _make_sqlite_row({"handle": "bar", "title": "Bar"})
    assert _row_get(row, "handle") == "bar"
    assert _row_get(row, "title") == "Bar"
    assert _row_get(row, "missing") is None
    assert _row_get(row, "missing", "default") == "default"


def test_row_get_with_none():
    assert _row_get(None, "handle") is None
    assert _row_get(None, "handle", "default") == "default"


def test_collection_handle_extraction_from_sqlite_rows():
    """Regression test: extracting handles from sqlite3.Row collections must not raise AttributeError."""
    rows = [
        _make_sqlite_row({"handle": "collection-a", "title": "Collection A"}),
        _make_sqlite_row({"handle": "collection-b", "title": "Collection B"}),
        _make_sqlite_row({"handle": "", "title": "Empty Handle"}),
    ]
    handles = [_row_get(c, "handle") for c in rows if _row_get(c, "handle")]
    assert handles == ["collection-a", "collection-b"]


def test_mixed_dict_and_row_extraction():
    """Ensure extraction works with a mix of dicts and Rows."""
    items = [
        {"handle": "dict-item", "title": "Dict Item"},
        _make_sqlite_row({"handle": "row-item", "title": "Row Item"}),
    ]
    handles = [_row_get(c, "handle") for c in items if _row_get(c, "handle")]
    assert handles == ["dict-item", "row-item"]
