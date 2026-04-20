import sqlite3
from pathlib import Path

import pytest

from shopifyseo.sqlite_utf8 import configure_sqlite_text_decode, utf8_text_factory


def test_utf8_text_factory_replaces_invalid_bytes():
    assert utf8_text_factory(b"ok") == "ok"
    assert utf8_text_factory(b"a\xff\xfeb") == "a\ufffd\ufffdb"


def test_configure_sqlite_text_decode_reads_attached_recovered_catalog():
    """Recovered catalogs can contain invalid UTF-8 in TEXT; ATTACH enforces strict decode."""
    catalog = Path(__file__).resolve().parents[1] / "shopify_catalog.sqlite3"
    if not catalog.exists():
        pytest.skip("shopify_catalog.sqlite3 not in workspace")

    mem = sqlite3.connect(":memory:")
    mem.execute("ATTACH DATABASE ? AS cat", (str(catalog),))
    mem.row_factory = sqlite3.Row
    probe = mem.execute("SELECT 1 FROM cat.sqlite_master WHERE name='products'").fetchone()
    if not probe:
        pytest.skip("no products table")

    has_r42 = mem.execute("SELECT 1 FROM cat.products WHERE rowid=42").fetchone()
    if not has_r42:
        pytest.skip("no products rowid=42 (corrupt-row fixture absent)")

    with pytest.raises(sqlite3.OperationalError):
        mem.execute("SELECT index_status FROM cat.products WHERE rowid=42").fetchone()

    configure_sqlite_text_decode(mem)
    row = mem.execute("SELECT index_status FROM cat.products WHERE rowid=42").fetchone()
    assert isinstance(row["index_status"], str)
