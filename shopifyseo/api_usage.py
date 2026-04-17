"""API cost tracking: Gemini token usage (estimated from pricing), DataForSEO (bill from API),
and dashboard summaries.

Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing (Standard tier, April 2026).
All Gemini prices are per 1 million tokens in USD.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table — (input_per_1m, output_per_1m)
# Image output models use a separate image token rate ($60/1M for flash-image,
# $120/1M for pro-image), which we encode as the output rate.
# ---------------------------------------------------------------------------

GEMINI_PRICING: dict[str, tuple[float, float]] = {
    # Gemini 3.1 family
    "gemini-3.1-pro-preview":              (2.00, 12.00),
    "gemini-3.1-pro-preview-customtools":  (2.00, 12.00),
    "gemini-3.1-flash-lite-preview":       (0.25, 1.50),
    "gemini-3.1-flash-image-preview":      (0.50, 60.00),  # image output tokens at $60/1M
    "gemini-3.1-flash-live-preview":       (0.75, 4.50),
    # Gemini 3 family
    "gemini-3-flash-preview":              (0.50, 3.00),
    "gemini-3-pro-image-preview":          (2.00, 120.00),  # image output tokens at $120/1M
    # Gemini 2.5 family
    "gemini-2.5-pro":                      (1.25, 10.00),
    "gemini-2.5-flash":                    (0.30, 2.50),
    "gemini-2.5-flash-lite":               (0.10, 0.40),
    "gemini-2.5-flash-image":              (0.30, 30.00),  # image output tokens at $30/1M
    # Gemini 2.0 family (deprecated June 2026)
    "gemini-2.0-flash":                    (0.10, 0.40),
    "gemini-2.0-flash-lite":               (0.075, 0.30),
    # Embeddings
    "gemini-embedding-2-preview":          (0.20, 0.0),
    "gemini-embedding-001":                (0.15, 0.0),
}

# Fallback for models not in the table (e.g. future releases)
_DEFAULT_PRICING = (0.30, 2.50)


def _lookup_pricing(model: str) -> tuple[float, float]:
    """Return (input_per_1m, output_per_1m) for a model, with fallback."""
    clean = model.strip().removeprefix("models/")
    if clean in GEMINI_PRICING:
        return GEMINI_PRICING[clean]
    for key in GEMINI_PRICING:
        if clean.startswith(key):
            return GEMINI_PRICING[key]
    return _DEFAULT_PRICING


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp_rate, out_rate = _lookup_pricing(model)
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


# ---------------------------------------------------------------------------
# Token extraction from Gemini responses
# ---------------------------------------------------------------------------

def extract_usage_metadata(response: dict) -> tuple[int, int, int]:
    """Extract (prompt_tokens, candidate_tokens, total_tokens) from a Gemini response."""
    meta = response.get("usageMetadata") or {}
    prompt = meta.get("promptTokenCount") or 0
    candidates = meta.get("candidatesTokenCount") or 0
    total = meta.get("totalTokenCount") or (prompt + candidates)
    return int(prompt), int(candidates), int(total)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    from .dashboard_store import db_connect
    return db_connect()


def log_api_usage(
    *,
    provider: str,
    model: str,
    call_type: str,
    stage: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    estimated_cost_override_usd: float | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Insert a usage row. Opens its own connection if none provided.

    When ``estimated_cost_override_usd`` is set (e.g. DataForSEO response ``cost``), it is
    stored as ``estimated_cost_usd`` and token-based Gemini pricing is skipped.
    """
    if estimated_cost_override_usd is not None:
        cost = float(estimated_cost_override_usd)
    else:
        cost = compute_cost(model, input_tokens, output_tokens)
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    own_conn = False
    try:
        if conn is None:
            conn = _get_conn()
            own_conn = True
        conn.execute(
            """
            INSERT INTO api_usage_log
                (provider, model, call_type, stage, input_tokens, output_tokens, total_tokens, estimated_cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, model, call_type, stage or "", input_tokens, output_tokens, total_tokens, cost),
        )
        conn.commit()
    except Exception:
        logger.debug("Failed to log API usage", exc_info=True)
    finally:
        if own_conn and conn:
            conn.close()


# ---------------------------------------------------------------------------
# Stage → business process mapping
# ---------------------------------------------------------------------------

_STAGE_PREFIX_TO_PROCESS: list[tuple[str, str]] = [
    ("single_field_generate:", "SEO Generation"),
    ("single_field_review:", "Content Review"),
    ("article_ideas", "Article Ideas"),
    ("article_draft", "Article Drafting"),
    ("sidekick", "Sidekick Chat"),
    ("cluster_matching", "Keyword Clustering"),
    ("clustering", "Keyword Clustering"),
    ("image_generation", "Image Generation"),
    ("vision_caption", "Image Optimization"),
    ("embedding_sync", "Embedding Sync"),
    ("settings_test", "Other"),
]


def _stage_to_process_sql() -> str:
    """Build a SQL CASE expression that maps stage values to process names."""
    clauses = []
    for prefix, process in _STAGE_PREFIX_TO_PROCESS:
        if prefix.endswith(":"):
            clauses.append(f"WHEN stage LIKE '{prefix}%' THEN '{process}'")
        else:
            clauses.append(f"WHEN stage = '{prefix}' THEN '{process}'")
    return "CASE " + " ".join(clauses) + " ELSE 'Other' END"


# ---------------------------------------------------------------------------
# Summaries for the dashboard
# ---------------------------------------------------------------------------

_LLM_FILTER = "provider != 'dataforseo'"
_SEO_FILTER = "provider = 'dataforseo'"


def get_usage_summary(conn: sqlite3.Connection, days: int = 30) -> dict:
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_1d = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_custom = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def _scalar(sql: str, params: tuple = ()) -> dict:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return {"total_cost": 0.0, "total_calls": 0, "total_input_tokens": 0, "total_output_tokens": 0}
        return {
            "total_cost": row["total_cost"] or 0.0,
            "total_calls": row["total_calls"] or 0,
            "total_input_tokens": row["total_input_tokens"] or 0,
            "total_output_tokens": row["total_output_tokens"] or 0,
        }

    agg_sql_llm = f"""
        SELECT
            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
            COUNT(*) AS total_calls,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens
        FROM api_usage_log
        WHERE created_at >= ? AND {_LLM_FILTER}
    """

    agg_all_llm_sql = f"""
        SELECT
            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
            COUNT(*) AS total_calls,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens
        FROM api_usage_log
        WHERE {_LLM_FILTER}
    """

    agg_sql_seo = f"""
        SELECT
            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
            COUNT(*) AS total_calls,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens
        FROM api_usage_log
        WHERE created_at >= ? AND {_SEO_FILTER}
    """

    agg_all_seo_sql = f"""
        SELECT
            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost,
            COUNT(*) AS total_calls,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens
        FROM api_usage_log
        WHERE {_SEO_FILTER}
    """

    summary_today = _scalar(agg_sql_llm, (cutoff_1d,))
    summary_7d = _scalar(agg_sql_llm, (cutoff_7d,))
    summary_30d = _scalar(agg_sql_llm, (cutoff_30d,))
    summary_all = _scalar(agg_all_llm_sql)

    seo_summary_today = _scalar(agg_sql_seo, (cutoff_1d,))
    seo_summary_7d = _scalar(agg_sql_seo, (cutoff_7d,))
    seo_summary_30d = _scalar(agg_sql_seo, (cutoff_30d,))
    seo_summary_all = _scalar(agg_all_seo_sql)

    by_model_rows = conn.execute(
        f"""
        SELECT model,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_LLM_FILTER}
        GROUP BY model
        ORDER BY cost DESC
        """,
        (cutoff_custom,),
    ).fetchall()

    by_call_type_rows = conn.execute(
        f"""
        SELECT call_type,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_LLM_FILTER}
        GROUP BY call_type
        ORDER BY cost DESC
        """,
        (cutoff_custom,),
    ).fetchall()

    by_stage_rows = conn.execute(
        f"""
        SELECT stage,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_LLM_FILTER}
        GROUP BY stage
        ORDER BY cost DESC
        """,
        (cutoff_custom,),
    ).fetchall()

    process_expr = _stage_to_process_sql()
    by_process_rows = conn.execute(
        f"""
        SELECT {process_expr} AS process,
               COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_LLM_FILTER}
        GROUP BY process
        ORDER BY cost DESC
        """,
        (cutoff_custom,),
    ).fetchall()

    daily_rows = conn.execute(
        f"""
        SELECT DATE(created_at) AS day,
               COUNT(*) AS calls,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_LLM_FILTER}
        GROUP BY DATE(created_at)
        ORDER BY day ASC
        """,
        (cutoff_custom,),
    ).fetchall()

    recent_rows = conn.execute(
        f"""
        SELECT id, provider, model, call_type, stage, input_tokens, output_tokens,
               total_tokens, estimated_cost_usd, created_at
        FROM api_usage_log
        WHERE {_LLM_FILTER}
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    seo_daily_rows = conn.execute(
        f"""
        SELECT DATE(created_at) AS day,
               COUNT(*) AS calls,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_SEO_FILTER}
        GROUP BY DATE(created_at)
        ORDER BY day ASC
        """,
        (cutoff_custom,),
    ).fetchall()

    seo_by_endpoint_rows = conn.execute(
        f"""
        SELECT model AS endpoint,
               COUNT(*) AS calls,
               COALESCE(SUM(estimated_cost_usd), 0) AS cost
        FROM api_usage_log
        WHERE created_at >= ? AND {_SEO_FILTER}
        GROUP BY model
        ORDER BY cost DESC
        """,
        (cutoff_custom,),
    ).fetchall()

    seo_recent_rows = conn.execute(
        f"""
        SELECT id, provider, model, call_type, stage, input_tokens, output_tokens,
               total_tokens, estimated_cost_usd, created_at
        FROM api_usage_log
        WHERE {_SEO_FILTER}
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()

    return {
        "periods": {
            "today": summary_today,
            "last_7d": summary_7d,
            "last_30d": summary_30d,
            "all_time": summary_all,
        },
        "by_model": [dict(r) for r in by_model_rows],
        "by_call_type": [dict(r) for r in by_call_type_rows],
        "by_process": [dict(r) for r in by_process_rows],
        "by_stage": [dict(r) for r in by_stage_rows],
        "daily": [dict(r) for r in daily_rows],
        "recent": [dict(r) for r in recent_rows],
        "days": days,
        "seo": {
            "periods": {
                "today": seo_summary_today,
                "last_7d": seo_summary_7d,
                "last_30d": seo_summary_30d,
                "all_time": seo_summary_all,
            },
            "daily": [dict(r) for r in seo_daily_rows],
            "by_endpoint": [dict(r) for r in seo_by_endpoint_rows],
            "recent": [dict(r) for r in seo_recent_rows],
        },
    }
