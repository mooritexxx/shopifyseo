"""Tests for usage summary split between LLM and DataForSEO."""

import sqlite3

import pytest

from shopifyseo.api_usage import get_usage_summary, log_api_usage


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE api_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            call_type TEXT NOT NULL,
            stage TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return c


def test_log_api_usage_cost_override_skips_gemini_pricing(conn: sqlite3.Connection) -> None:
    log_api_usage(
        provider="dataforseo",
        model="/dataforseo_labs/google/keyword_ideas/live",
        call_type="seo_api",
        stage="",
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        estimated_cost_override_usd=0.042,
        conn=conn,
    )
    row = conn.execute("SELECT estimated_cost_usd FROM api_usage_log").fetchone()
    assert row is not None
    assert abs(row["estimated_cost_usd"] - 0.042) < 1e-9


def test_get_usage_summary_splits_llm_and_seo(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO api_usage_log
            (provider, model, call_type, stage, input_tokens, output_tokens, total_tokens, estimated_cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("gemini", "gemini-2.5-flash", "chat", "sidekick", 100, 50, 150, 0.01, "2026-04-01 12:00:00"),
            ("dataforseo", "/labs/foo", "seo_api", "", 0, 0, 0, 0.05, "2026-04-01 12:00:00"),
            ("dataforseo", "/labs/bar", "seo_api", "", 0, 0, 0, 0.03, "2026-04-02 12:00:00"),
        ],
    )
    conn.commit()

    summary = get_usage_summary(conn, days=30)

    assert summary["periods"]["all_time"]["total_cost"] == pytest.approx(0.01)
    assert summary["periods"]["all_time"]["total_calls"] == 1

    assert summary["seo"]["periods"]["all_time"]["total_cost"] == pytest.approx(0.08)
    assert summary["seo"]["periods"]["all_time"]["total_calls"] == 2

    endpoints = {r["endpoint"]: r["cost"] for r in summary["seo"]["by_endpoint"]}
    assert endpoints["/labs/foo"] == pytest.approx(0.05)
    assert endpoints["/labs/bar"] == pytest.approx(0.03)

    assert len(summary["recent"]) == 1
    assert summary["recent"][0]["provider"] == "gemini"

    assert len(summary["seo"]["recent"]) == 2
    assert {r["provider"] for r in summary["seo"]["recent"]} == {"dataforseo"}
