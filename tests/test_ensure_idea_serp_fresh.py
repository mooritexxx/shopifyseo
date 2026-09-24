"""Tests for ensure_idea_serp_fresh (auto-refresh SERP snapshot during draft generation)."""

import sqlite3
import time

import pytest

from shopifyseo.dashboard_queries import (
    SERP_FRESHNESS_TTL_SECONDS,
    ensure_idea_serp_fresh,
    save_article_ideas,
)
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_dashboard_schema(c)
    return c


def _save_minimal_idea(
    conn: sqlite3.Connection,
    *,
    primary_keyword: str = "test keyword",
    with_serp_data: bool = False,
    serp_refreshed_at: int | None = None,
) -> int:
    """Create an idea, optionally populating SERP data and timestamp."""
    ids = save_article_ideas(
        conn,
        [
            {
                "suggested_title": "Test Title",
                "brief": "Brief body.",
                "primary_keyword": primary_keyword,
                "supporting_keywords": [],
                "search_intent": "informational",
                "content_format": "",
                "estimated_monthly_traffic": 0,
                "linked_cluster_id": None,
                "linked_cluster_name": "",
                "linked_collection_handle": "",
                "linked_collection_title": "",
                "source_type": "cluster_gap",
                "gap_reason": "",
                "total_volume": 0,
                "avg_difficulty": 0.0,
                "opportunity_score": 0.0,
                "dominant_serp_features": "",
                "content_format_hints": "",
                "linked_keywords_json": "[]",
            }
        ],
    )
    idea_id = ids[0]

    if with_serp_data:
        conn.execute(
            """
            UPDATE article_ideas
            SET audience_questions_json = ?,
                top_ranking_pages_json = ?,
                serp_refreshed_at = ?
            WHERE id = ?
            """,
            (
                '[{"question": "Test Q?", "snippet": "Answer"}]',
                '[{"title": "Page 1", "url": "https://example.com/1"}]',
                serp_refreshed_at,
                idea_id,
            ),
        )
        conn.commit()

    return idea_id


def _set_serpapi_key(conn: sqlite3.Connection, key: str = "fake-key") -> None:
    """Set a fake SerpAPI key in settings."""
    conn.execute(
        "INSERT OR REPLACE INTO service_settings (key, value) VALUES (?, ?)",
        ("serpapi_api_key", key),
    )
    conn.commit()


class TestEnsureIdeaSerpFreshMissing:
    """Tests for when SERP data is missing."""

    def test_missing_serp_triggers_refresh(
        self, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
    ):
        """When SERP is missing, ensure_idea_serp_fresh should refresh."""
        idea_id = _save_minimal_idea(conn, with_serp_data=False)
        _set_serpapi_key(conn)

        refresh_called = []

        def fake_snapshot(_c: sqlite3.Connection, _kw: str, **_: object) -> dict:
            refresh_called.append(True)
            return {
                "audience_questions": [{"question": "New Q?", "snippet": "New A"}],
                "top_ranking_pages": [{"title": "New Page", "url": "https://new.com"}],
                "ai_overview": None,
                "related_searches": [],
                "paa_expansion": [],
            }

        monkeypatch.setattr(
            "shopifyseo.audience_questions_api.fetch_serpapi_primary_keyword_snapshot",
            fake_snapshot,
        )

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "refreshed"
        assert result["refreshed"] is True
        assert "missing" in result["reason"].lower()
        assert result["error"] is None
        assert len(refresh_called) == 1

    def test_missing_serp_no_serpapi_key_fails(self, conn: sqlite3.Connection):
        """When SERP is missing and no SerpAPI key, should fail."""
        idea_id = _save_minimal_idea(conn, with_serp_data=False)

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "failed"
        assert result["refreshed"] is False
        assert "SerpAPI" in (result["error"] or "")

    def test_missing_serp_no_primary_keyword_fails(self, conn: sqlite3.Connection):
        """When SERP is missing and no primary keyword, should fail."""
        idea_id = _save_minimal_idea(conn, primary_keyword="", with_serp_data=False)
        _set_serpapi_key(conn)

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "failed"
        assert result["refreshed"] is False
        assert "keyword" in (result["error"] or "").lower()


class TestEnsureIdeaSerpFreshFresh:
    """Tests for when SERP data is fresh."""

    def test_fresh_serp_reuses_existing(self, conn: sqlite3.Connection):
        """When SERP is fresh, should reuse existing without refresh."""
        now = int(time.time())
        idea_id = _save_minimal_idea(
            conn, with_serp_data=True, serp_refreshed_at=now - 3600  # 1 hour ago
        )
        _set_serpapi_key(conn)

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "reused"
        assert result["refreshed"] is False
        assert "fresh" in result["reason"].lower()
        assert result["error"] is None

    def test_custom_max_age_respects_threshold(self, conn: sqlite3.Connection):
        """Custom max_age_seconds should control freshness threshold."""
        now = int(time.time())
        idea_id = _save_minimal_idea(
            conn, with_serp_data=True, serp_refreshed_at=now - 300  # 5 min ago
        )
        _set_serpapi_key(conn)

        result = ensure_idea_serp_fresh(conn, idea_id, max_age_seconds=600)

        assert result["status"] == "reused"
        assert result["refreshed"] is False


class TestEnsureIdeaSerpFreshStale:
    """Tests for when SERP data is stale (older than TTL)."""

    def test_stale_serp_triggers_refresh(
        self, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
    ):
        """When SERP is stale, should refresh."""
        now = int(time.time())
        idea_id = _save_minimal_idea(
            conn,
            with_serp_data=True,
            serp_refreshed_at=now - SERP_FRESHNESS_TTL_SECONDS - 3600,  # >24h ago
        )
        _set_serpapi_key(conn)

        refresh_called = []

        def fake_snapshot(_c: sqlite3.Connection, _kw: str, **_: object) -> dict:
            refresh_called.append(True)
            return {
                "audience_questions": [{"question": "Updated Q?", "snippet": "Updated A"}],
                "top_ranking_pages": [],
                "ai_overview": None,
                "related_searches": [],
                "paa_expansion": [],
            }

        monkeypatch.setattr(
            "shopifyseo.audience_questions_api.fetch_serpapi_primary_keyword_snapshot",
            fake_snapshot,
        )

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "refreshed"
        assert result["refreshed"] is True
        assert "old" in result["reason"].lower() or "threshold" in result["reason"].lower()
        assert result["error"] is None
        assert len(refresh_called) == 1

    def test_legacy_data_no_timestamp_triggers_refresh(
        self, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
    ):
        """Legacy SERP data without timestamp should refresh."""
        idea_id = _save_minimal_idea(
            conn, with_serp_data=True, serp_refreshed_at=None  # No timestamp
        )
        _set_serpapi_key(conn)

        refresh_called = []

        def fake_snapshot(_c: sqlite3.Connection, _kw: str, **_: object) -> dict:
            refresh_called.append(True)
            return {
                "audience_questions": [],
                "top_ranking_pages": [],
                "ai_overview": None,
                "related_searches": [],
                "paa_expansion": [],
            }

        monkeypatch.setattr(
            "shopifyseo.audience_questions_api.fetch_serpapi_primary_keyword_snapshot",
            fake_snapshot,
        )

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "refreshed"
        assert result["refreshed"] is True
        assert "legacy" in result["reason"].lower() or "no timestamp" in result["reason"].lower()
        assert len(refresh_called) == 1


class TestEnsureIdeaSerpFreshFailure:
    """Tests for refresh failure handling."""

    def test_refresh_failure_returns_error(
        self, monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
    ):
        """When refresh fails, should return failure status with error."""
        idea_id = _save_minimal_idea(conn, with_serp_data=False)
        _set_serpapi_key(conn)

        def fake_snapshot_fails(_c: sqlite3.Connection, _kw: str, **_: object) -> dict:
            return {"serpapi_error": "Rate limit exceeded"}

        monkeypatch.setattr(
            "shopifyseo.audience_questions_api.fetch_serpapi_primary_keyword_snapshot",
            fake_snapshot_fails,
        )

        result = ensure_idea_serp_fresh(conn, idea_id)

        assert result["status"] == "failed"
        assert result["refreshed"] is False
        assert "rate limit" in (result["error"] or "").lower() or "usable" in (result["error"] or "").lower()

    def test_idea_not_found_raises(self, conn: sqlite3.Connection):
        """When idea doesn't exist, should raise LookupError."""
        with pytest.raises(LookupError, match="not found"):
            ensure_idea_serp_fresh(conn, 99999)


class TestSerpFreshnessTtlConstant:
    """Tests for the TTL constant."""

    def test_default_ttl_is_24_hours(self):
        """SERP_FRESHNESS_TTL_SECONDS should default to 24 hours."""
        assert SERP_FRESHNESS_TTL_SECONDS == 24 * 60 * 60
        assert SERP_FRESHNESS_TTL_SECONDS == 86400
