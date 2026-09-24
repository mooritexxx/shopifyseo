"""gsc_query_rows materialization: read-limit cap and wipe protection."""

import sqlite3

from shopifyseo import dashboard_store
from shopifyseo.gsc_query_limits import GSC_PER_URL_QUERY_ROW_LIMIT


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE gsc_query_rows (
          object_type TEXT NOT NULL,
          object_handle TEXT NOT NULL,
          url TEXT NOT NULL,
          query TEXT NOT NULL,
          clicks INTEGER,
          impressions INTEGER,
          ctr REAL,
          position REAL,
          fetched_at INTEGER,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(object_type, object_handle, query)
        )
        """
    )
    return conn


def _detail(n_rows: int, *, exists: bool = True) -> dict:
    return {
        "query_rows": [
            {"keys": [f"q{i}"], "clicks": 1000 - i, "impressions": i, "ctr": 0.1, "position": 2.0}
            for i in range(n_rows)
        ],
        "_cache": {"exists": exists, "stale": False, "fetched_at": 123, "expires_at": 999},
    }


def _stored(conn: sqlite3.Connection) -> list[str]:
    return [
        r["query"]
        for r in conn.execute("SELECT query FROM gsc_query_rows ORDER BY clicks DESC").fetchall()
    ]


def test_materializes_at_most_the_read_limit() -> None:
    """The cache payload keeps a superset; only what readers consume is written."""
    conn = _conn()
    dashboard_store._write_gsc_per_url_query_caches(conn, "product", "w", "https://x/w", _detail(100))

    stored = _stored(conn)
    assert len(stored) == GSC_PER_URL_QUERY_ROW_LIMIT
    # Payload order is preserved, so the highest-click rows are the ones kept.
    assert stored == [f"q{i}" for i in range(GSC_PER_URL_QUERY_ROW_LIMIT)]
    conn.close()


def test_fewer_rows_than_the_limit_are_all_kept() -> None:
    conn = _conn()
    dashboard_store._write_gsc_per_url_query_caches(conn, "product", "w", "https://x/w", _detail(3))
    assert _stored(conn) == ["q0", "q1", "q2"]
    conn.close()


def test_missing_cache_row_does_not_wipe_stored_rows() -> None:
    """exists=False means 'no data for this object', not 'this object has no queries'."""
    conn = _conn()
    dashboard_store._write_gsc_per_url_query_caches(conn, "product", "w", "https://x/w", _detail(5))
    assert len(_stored(conn)) == 5

    empty = {"query_rows": [], "_cache": {"exists": False, "stale": True, "fetched_at": None}}
    dashboard_store._write_gsc_per_url_query_caches(conn, "product", "w", "https://x/w", empty)

    assert len(_stored(conn)) == 5, "rows from a previous sync must survive a cache miss"
    conn.close()


def test_present_but_empty_payload_still_clears_rows() -> None:
    """A real cache row saying 'no queries' is authoritative and should clear."""
    conn = _conn()
    dashboard_store._write_gsc_per_url_query_caches(conn, "product", "w", "https://x/w", _detail(5))
    assert len(_stored(conn)) == 5

    dashboard_store._write_gsc_per_url_query_caches(
        conn, "product", "w", "https://x/w", _detail(0, exists=True)
    )
    assert _stored(conn) == []
    conn.close()


def test_stale_payload_is_still_applied() -> None:
    """Expired-but-present cache carries the last known data and must not be skipped."""
    conn = _conn()
    stale_detail = _detail(4)
    stale_detail["_cache"]["stale"] = True
    dashboard_store._write_gsc_per_url_query_caches(conn, "product", "w", "https://x/w", stale_detail)
    assert len(_stored(conn)) == 4
    conn.close()
