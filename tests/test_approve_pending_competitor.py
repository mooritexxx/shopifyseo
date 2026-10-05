"""Approve-pending-competitor upsert (PR 4). Main 500'd after committing the pending removal."""
import sqlite3

import pytest

from backend.app.routers import keywords as keywords_router
from backend.app.services.keyword_research import save_competitor_discovery_pending
from shopifyseo.dashboard_store import ensure_dashboard_schema


PENDING = {
    "domain": "rivalvape.com",
    "keywords_common": 12,
    "keywords_they_have": 40,
    "keywords_we_have": 8,
    "share": 0.25,
    "traffic": 900,
    "labs_visibility": 1.5,
    "labs_avg_position": 11,
    "labs_median_position": 9,
    "labs_seed_etv": 100,
    "labs_bulk_etv": 200,
    "labs_rating": 3,
}


@pytest.fixture
def approve_db(tmp_path, monkeypatch):
    path = tmp_path / "approve.db"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connect()
    ensure_dashboard_schema(conn)
    conn.commit()
    conn.close()
    monkeypatch.setattr(keywords_router, "open_db_connection", connect)
    return connect


def _profile(connect, domain: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT domain, keywords_common, keywords_they_have, keywords_we_have, "
            "share, traffic, is_manual, labs_visibility, labs_avg_position, "
            "labs_median_position, labs_seed_etv, labs_bulk_etv, labs_rating, "
            "authority_score, authority_rank, referring_domains "
            "FROM competitor_profiles WHERE domain = ?",
            (domain,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_approve_new_domain_inserts_manual_profile(approve_db):
    conn = approve_db()
    try:
        save_competitor_discovery_pending(conn, [PENDING])
    finally:
        conn.close()

    result = keywords_router.approve_pending_competitor("rivalvape.com")
    assert result["ok"] is True
    row = _profile(approve_db, "rivalvape.com")
    assert row is not None
    assert row["is_manual"] == 1
    assert row["keywords_common"] == 12
    assert row["keywords_they_have"] == 40
    assert row["keywords_we_have"] == 8
    assert row["share"] == pytest.approx(0.25)
    assert row["traffic"] == 900
    assert row["labs_visibility"] == pytest.approx(1.5)
    assert row["labs_avg_position"] == 11
    assert row["labs_median_position"] == 9
    assert row["labs_seed_etv"] == 100
    assert row["labs_bulk_etv"] == 200
    assert row["labs_rating"] == 3
    assert all(
        p.get("domain") != "rivalvape.com" for p in result["data"]["pending_suggestions"]
    )


def test_approve_existing_domain_updates_counts_preserves_authority(approve_db):
    conn = approve_db()
    try:
        conn.execute(
            """
            INSERT INTO competitor_profiles
                (domain, keywords_common, keywords_they_have, keywords_we_have,
                 share, traffic, is_manual, updated_at,
                 authority_score, authority_rank, referring_domains)
            VALUES (?, 1, 2, 3, 0.01, 10, 0, 1, 42.5, 99, 7)
            """,
            ("rivalvape.com",),
        )
        conn.commit()
        save_competitor_discovery_pending(conn, [PENDING])
    finally:
        conn.close()

    keywords_router.approve_pending_competitor("rivalvape.com")
    row = _profile(approve_db, "rivalvape.com")
    assert row["is_manual"] == 1
    assert row["keywords_common"] == 12
    assert row["keywords_they_have"] == 40
    assert row["keywords_we_have"] == 8
    assert row["share"] == pytest.approx(0.25)
    assert row["traffic"] == 900
    assert row["authority_score"] == pytest.approx(42.5)
    assert row["authority_rank"] == 99
    assert row["referring_domains"] == 7
