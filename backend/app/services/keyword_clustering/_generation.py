"""AI-driven cluster generation and page-matching."""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from shopifyseo.dashboard_google import get_service_setting
from shopifyseo.dashboard_ai_engine_parts.generation import (
    _call_ai,
    _require_provider_credentials,
    ai_settings,
)

from ._helpers import _build_clustering_prompt, _compute_cluster_stats, _group_by_parent_topic
from ._storage import TARGET_KEY, load_clusters

logger = logging.getLogger(__name__)

CLUSTERING_SCHEMA = {
    "name": "clustering_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "content_type": {"type": "string"},
                        "primary_keyword": {"type": "string"},
                        "content_brief": {"type": "string"},
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "name",
                        "content_type",
                        "primary_keyword",
                        "content_brief",
                        "keywords",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["clusters"],
        "additionalProperties": False,
    },
}

MATCHING_SCHEMA = {
    "name": "matching_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cluster_name": {"type": "string"},
                        "match_type": {"type": "string"},
                        "match_handle": {"type": "string"},
                        "match_title": {"type": "string"},
                    },
                    "required": ["cluster_name", "match_type", "match_handle", "match_title"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["matches"],
        "additionalProperties": False,
    },
}


def _match_clusters_to_pages(
    conn: sqlite3.Connection,
    clusters: list[dict],
    settings: dict,
) -> list[dict]:
    """Match clusters to existing Shopify pages using LLM.

    Returns the clusters list with 'suggested_match' populated.
    On LLM failure, returns clusters with suggested_match = None.
    """
    # 1. Query existing pages
    collections = conn.execute("SELECT handle, title FROM collections ORDER BY title").fetchall()
    pages = conn.execute("SELECT handle, title FROM pages ORDER BY title").fetchall()
    articles = conn.execute(
        "SELECT blog_handle, handle, title FROM blog_articles ORDER BY blog_handle, title"
    ).fetchall()

    # 2. Build available pages list
    available: list[dict] = []
    for row in collections:
        available.append({"type": "collection", "handle": row[0], "title": row[1]})
    for row in pages:
        available.append({"type": "page", "handle": row[0], "title": row[1]})
    for row in articles:
        composite_handle = f"{row[0]}/{row[1]}"
        available.append({"type": "blog_article", "handle": composite_handle, "title": row[2]})

    # 3. If no pages exist, skip matching
    if not available:
        for c in clusters:
            c["suggested_match"] = None
        return clusters

    # 4. Build matching prompt
    cluster_summaries = [
        {
            "name": c["name"],
            "content_type": c.get("content_type", ""),
            "primary_keyword": c.get("primary_keyword", ""),
            "keywords": c.get("keywords", [])[:10],
        }
        for c in clusters
    ]

    system_prompt = (
        "You are an SEO strategist matching keyword clusters to existing website pages.\n\n"
        "For each cluster, pick the best matching page from the available pages list, "
        "or mark it as 'new' if no existing page is a good fit.\n\n"
        "Guidelines:\n"
        "- **Align Shopify type with cluster content_type when a reasonable topical match exists:** "
        "collection_page → collection; blog_post or buying_guide → blog_article; "
        "landing_page → page; product_page → collection or product if clearly appropriate.\n"
        "- **Do not** map blog_post or buying_guide to a static `page` when any blog_article in the list "
        "is a plausible topical match — prefer the blog article unless it is clearly off-topic.\n"
        "- Match based on topical relevance between cluster keywords and page title/handle.\n"
        "- If no existing page covers the cluster's topic well, set match_type to 'new' "
        "with empty match_handle and match_title.\n\n"
        "match_type must be one of: 'collection', 'page', 'blog_article', 'new'.\n"
        "When match_type is 'new', set match_handle and match_title to empty strings."
    )

    user_prompt = (
        "Clusters to match:\n"
        + json.dumps(cluster_summaries, indent=2)
        + "\n\nAvailable pages:\n"
        + json.dumps(available, indent=2)
    )

    # 5. Call LLM
    provider = settings["generation_provider"]
    model = settings["generation_model"]

    try:
        llm_result = _call_ai(
            settings=settings,
            provider=provider,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=settings["timeout"],
            json_schema=MATCHING_SCHEMA,
            stage="cluster_matching",
        )
    except Exception:
        logger.exception("Cluster-to-page matching failed; clusters saved without matches")
        for c in clusters:
            c["suggested_match"] = None
        return clusters

    # 6. Apply matches to clusters
    matches_by_name: dict[str, dict] = {}
    for m in llm_result.get("matches", []):
        matches_by_name[m.get("cluster_name", "")] = m

    for c in clusters:
        m = matches_by_name.get(c["name"])
        if m and m.get("match_type") != "new":
            c["suggested_match"] = {
                "match_type": m["match_type"],
                "match_handle": m["match_handle"],
                "match_title": m["match_title"],
            }
        elif m and m.get("match_type") == "new":
            c["suggested_match"] = {
                "match_type": "new",
                "match_handle": "",
                "match_title": "",
            }
        else:
            c["suggested_match"] = None

    return clusters


def generate_clusters(
    conn: sqlite3.Connection,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Generate keyword clusters from approved target keywords using LLM."""

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # 1. Load approved target keywords
    progress("Loading approved keywords…")
    raw = get_service_setting(conn, TARGET_KEY, "{}")
    try:
        target_data = json.loads(raw)
    except json.JSONDecodeError:
        target_data = {}

    all_items = target_data.get("items", [])
    approved = [item for item in all_items if item.get("status") == "approved"]

    if not approved:
        raise RuntimeError("No approved keywords to cluster. Approve target keywords first.")

    # 2. Validate AI settings
    settings = ai_settings(conn)
    provider = settings["generation_provider"]
    model = settings["generation_model"]
    _require_provider_credentials(settings, provider)

    # 3. Group by parent_topic
    groups, orphans = _group_by_parent_topic(approved)
    progress(
        f"Grouped by parent topic — {len(groups)} groups, {len(orphans)} orphans"
    )

    # 4. Build prompt and call LLM
    progress(f"Refining clusters with AI ({provider}/{model})…")
    from shopifyseo.market_context import get_primary_country_code, country_display_name
    _mkt_name = country_display_name(get_primary_country_code(conn))
    system_prompt, user_prompt = _build_clustering_prompt(groups, orphans, country_name=_mkt_name)

    llm_result = _call_ai(
        settings=settings,
        provider=provider,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=settings["timeout"],
        json_schema=CLUSTERING_SCHEMA,
        stage="clustering",
    )

    # 5. Compute stats per cluster
    keywords_map = {item["keyword"].lower(): item for item in approved}
    clusters = []
    for raw_cluster in llm_result.get("clusters", []):
        kw_list = [k for k in raw_cluster.get("keywords", []) if k.lower() in keywords_map]
        if not kw_list:
            continue
        stats = _compute_cluster_stats(
            [k.lower() for k in kw_list], keywords_map
        )
        clusters.append({
            "name": raw_cluster.get("name", "Unnamed Cluster"),
            "content_type": raw_cluster.get("content_type", "blog_post"),
            "primary_keyword": raw_cluster.get("primary_keyword", kw_list[0]),
            "content_brief": raw_cluster.get("content_brief", ""),
            "keywords": kw_list,
            **stats,
        })

    # 6. Sort by total opportunity descending
    clusters.sort(key=lambda c: c.get("avg_opportunity", 0), reverse=True)

    # 7. Match clusters to existing pages
    progress("Matching clusters to existing pages…")
    try:
        clusters = _match_clusters_to_pages(conn, clusters, settings)
    except Exception:
        logger.exception("Matching step failed; saving clusters without matches")
        for c in clusters:
            if "suggested_match" not in c:
                c["suggested_match"] = None

    matched_count = sum(
        1 for c in clusters
        if c.get("suggested_match") and c["suggested_match"].get("match_type") not in (None, "new")
    )

    # 8. Save to DB
    generated_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM clusters")  # CASCADE deletes cluster_keywords
    for cluster in clusters:
        sm = cluster.get("suggested_match")
        match_type = sm.get("match_type") if sm else None
        match_handle = sm.get("match_handle", "") if sm else None
        match_title = sm.get("match_title", "") if sm else None

        conn.execute(
            """INSERT INTO clusters
               (name, content_type, primary_keyword, content_brief,
                total_volume, avg_difficulty, avg_opportunity,
                dominant_serp_features, content_format_hints, avg_cps,
                match_type, match_handle, match_title, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster["name"],
                cluster.get("content_type", "blog_post"),
                cluster.get("primary_keyword", ""),
                cluster.get("content_brief", ""),
                cluster.get("total_volume", 0),
                cluster.get("avg_difficulty", 0.0),
                cluster.get("avg_opportunity", 0.0),
                (cluster.get("dominant_serp_features") or "").strip(),
                (cluster.get("content_format_hints") or "").strip(),
                float(cluster.get("avg_cps") or 0.0),
                match_type,
                match_handle,
                match_title,
                generated_at,
            ),
        )
        cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        cluster["id"] = cluster_id
        for kw in cluster.get("keywords", []):
            conn.execute(
                "INSERT OR IGNORE INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
                (cluster_id, kw),
            )
    conn.commit()

    for c in clusters:
        st: dict[str, str | float] = {}
        dsf = (c.get("dominant_serp_features") or "").strip()
        if dsf:
            st["dominant_serp_features"] = dsf
        cfh = (c.get("content_format_hints") or "").strip()
        if cfh:
            st["content_format_hints"] = cfh
        ac = c.get("avg_cps")
        st["avg_cps"] = float(ac if ac is not None else 0.0)
        c["stats"] = st

    progress(f"Done — {len(clusters)} clusters generated, {matched_count} matched to existing pages")

    return {"clusters": clusters, "generated_at": generated_at}
