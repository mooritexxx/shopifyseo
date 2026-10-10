import sqlite3

import pytest

from shopifyseo.sqlite_utf8 import configure_sqlite_text_decode, utf8_text_factory


def test_utf8_text_factory_replaces_invalid_bytes():
    assert utf8_text_factory(b"ok") == "ok"
    assert utf8_text_factory(b"a\xff\xfeb") == "a\ufffd\ufffdb"


def test_configure_sqlite_text_decode_reads_attached_recovered_catalog(tmp_path):
    """Recovered catalogs can contain invalid UTF-8 in TEXT; ATTACH enforces strict decode."""
    # Self-contained fixture: a temp catalog with one TEXT value holding invalid UTF-8
    # (does not depend on the live shopify_catalog.sqlite3 or its rowid 42).
    catalog = tmp_path / "recovered_catalog.sqlite3"
    seed = sqlite3.connect(catalog)
    seed.execute("CREATE TABLE products (handle TEXT, index_status TEXT)")
    seed.execute("INSERT INTO products VALUES ('corrupt', CAST(x'61fffe62' AS TEXT))")
    seed.commit()
    seed.close()

    # SQLite-only on purpose: ATTACH + UTF-8 text factory.
    mem = sqlite3.connect(":memory:")
    mem.execute("ATTACH DATABASE ? AS cat", (str(catalog),))
    mem.row_factory = sqlite3.Row

    with pytest.raises(sqlite3.OperationalError):
        mem.execute("SELECT index_status FROM cat.products WHERE handle='corrupt'").fetchone()

    configure_sqlite_text_decode(mem)
    row = mem.execute("SELECT index_status FROM cat.products WHERE handle='corrupt'").fetchone()
    assert row["index_status"] == "a\ufffd\ufffdb"
