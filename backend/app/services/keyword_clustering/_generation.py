"""AI-driven cluster generation and page-matching."""
import concurrent.futures
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from backend.app.services.keyword_research.keyword_db import (
    load_approved_keywords,
    refresh_keyword_metric_opportunity_scores,
)
from shopifyseo.dashboard_ai_engine_parts.generation import (
    _call_ai,
    _require_provider_credentials,
    ai_settings,
)
from shopifyseo.dashboard_google import get_service_setting

from ._dedupe import collapse_near_duplicates
from ._helpers import _build_clustering_prompt, _compute_cluster_stats, _group_by_parent_topic
from ._postprocess import fold_singletons, merge_similar_clusters
from ._pre_cluster import pre_cluster
from ._scoring import cluster_priority_score, select_primary_keyword

CLUSTERING_MODE_KEY = "clustering_mode"
CLUSTERING_MAX_WORKERS = 4

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


def _load_keyword_vector_lookup(conn: sqlite3.Connection):
    """Load normalized keyword embedding vectors, or return an empty lookup."""
    try:
        import numpy as np
        from shopifyseo.embedding_store import _load_embedding_matrix
    except Exception:
        return None, {}
    try:
        matrix, meta = _load_embedding_matrix(conn, object_types=["keyword"])
    except Exception:
        logger.debug("Keyword embeddings unavailable for primary-keyword scoring", exc_info=True)
        return None, {}
    if matrix.shape[0] == 0:
        return np, {}

    lookup: dict[str, object] = {}
    for i, m in enumerate(meta):
        handle = (m.get("object_handle") or "").lower().strip()
        if not handle or handle in lookup:
            continue
        v = matrix[i].astype(np.float32)
        lookup[handle] = v / (np.linalg.norm(v) + 1e-10)
    return np, lookup


def _embedding_centrality_scores(
    keywords: list[str],
    np_mod,
    vector_lookup: dict[str, object],
) -> dict[str, float] | None:
    if np_mod is None or not vector_lookup:
        return None
    rows = [
        (kw.lower(), vector_lookup[kw.lower()])
        for kw in keywords
        if kw.lower() in vector_lookup
    ]
    if not rows:
        return None
    if len(rows) == 1:
        return {rows[0][0]: 100.0}
    matrix = np_mod.vstack([v for _, v in rows])
    centroid = np_mod.mean(matrix, axis=0)
    centroid = centroid / (np_mod.linalg.norm(centroid) + 1e-10)
    return {
        key: round(max(0.0, min(100.0, ((float(vec @ centroid) + 1.0) / 2.0) * 100.0)), 2)
        for key, vec in rows
    }


def _refresh_cluster_scoring(
    cluster: dict,
    keywords_map: dict[str, dict],
    np_mod,
    vector_lookup: dict[str, object],
) -> dict:
    kw_list = [kw for kw in cluster.get("keywords", []) if isinstance(kw, str) and kw.strip()]
    if not kw_list:
        return cluster
    content_type = cluster.get("content_type", "blog_post")
    centrality_scores = _embedding_centrality_scores(kw_list, np_mod, vector_lookup)
    stats = _compute_cluster_stats([kw.lower() for kw in kw_list], keywords_map)
    primary_keyword = select_primary_keyword(
        kw_list,
        keywords_map,
        ai_primary=cluster.get("primary_keyword", ""),
        content_type=content_type,
        centrality_scores=centrality_scores,
    )
    return {
        **cluster,
        "primary_keyword": primary_keyword,
        "keywords": kw_list,
        **stats,
        "priority_score": cluster_priority_score(kw_list, keywords_map),
    }


def _bucket_to_prompt(bucket: list[dict], country_name: str) -> tuple[str, str]:
    """Format a single pre-cluster bucket as a clustering-prompt payload."""
    if not bucket:
        return _build_clustering_prompt({}, [], country_name=country_name)
    topics = {(kw.get("parent_topic") or "").strip() for kw in bucket}
    if len(topics) == 1 and next(iter(topics)):
        return _build_clustering_prompt({next(iter(topics)): bucket}, [], country_name=country_name)
    return _build_clustering_prompt({}, bucket, country_name=country_name)


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

    # 1. Load approved target keywords from keyword_metrics (source of truth).
    progress("Refreshing opportunity scores…")
    refresh_keyword_metric_opportunity_scores(conn)
    progress("Loading approved keywords…")
    approved = load_approved_keywords(conn)

    if not approved:
        raise RuntimeError("No approved keywords to cluster. Approve target keywords first.")

    # 2. Validate AI settings
    settings = ai_settings(conn)
    provider = settings["generation_provider"]
    model = settings["generation_model"]
    _require_provider_credentials(settings, provider)

    # 3. Collapse embedding-similar near-duplicates into canonical+aliases so
    #    the LLM sees a cleaner, smaller payload.
    progress("Collapsing near-duplicate keywords…")
    canonicals, alias_map = collapse_near_duplicates(approved, conn)
    absorbed = sum(len(v) for v in alias_map.values())
    if absorbed:
        progress(
            f"Collapsed {absorbed} alias(es) into {len(alias_map)} canonical(s)"
        )

    # 4. Split canonicals into buckets (parallel mode) or keep as one (legacy).
    from shopifyseo.market_context import get_primary_country_code, country_display_name
    _mkt_name = country_display_name(get_primary_country_code(conn))

    mode = (get_service_setting(conn, CLUSTERING_MODE_KEY, "") or "parallel").strip().lower()
    if mode == "legacy":
        groups, orphans = _group_by_parent_topic(canonicals)
        progress(
            f"Legacy mode — {len(groups)} parent-topic groups, {len(orphans)} orphans, 1 LLM call"
        )
        buckets: list[list[dict]] = [canonicals]
        sys_p, user_p = _build_clustering_prompt(groups, orphans, country_name=_mkt_name)
        bucket_prompts: list[tuple[str, str]] = [(sys_p, user_p)]
    else:
        buckets = pre_cluster(canonicals, conn)
        progress(f"Pre-clustered into {len(buckets)} bucket(s)")
        bucket_prompts = [_bucket_to_prompt(b, _mkt_name) for b in buckets]

    # 5. Call LLM per bucket — in parallel when we have more than one bucket.
    progress(f"Refining clusters with AI ({provider}/{model})…")

    def _call_one(idx: int, messages: list[dict]) -> tuple[int, dict]:
        return idx, _call_ai(
            settings=settings,
            provider=provider,
            model=model,
            messages=messages,
            timeout=settings["timeout"],
            json_schema=CLUSTERING_SCHEMA,
            stage="clustering",
        )

    raw_clusters: list[dict] = []
    messages_by_idx = [
        [
            {"role": "system", "content": sp},
            {"role": "user", "content": up},
        ]
        for sp, up in bucket_prompts
    ]

    if len(messages_by_idx) == 1:
        _, llm_result = _call_one(0, messages_by_idx[0])
        raw_clusters.extend(llm_result.get("clusters", []))
    else:
        max_workers = min(len(messages_by_idx), CLUSTERING_MAX_WORKERS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {
                pool.submit(_call_one, i, msgs): i
                for i, msgs in enumerate(messages_by_idx)
            }
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    _, llm_result = fut.result()
                    bucket_clusters = llm_result.get("clusters", [])
                    raw_clusters.extend(bucket_clusters)
                    progress(f"Bucket {i + 1}/{len(messages_by_idx)} clustered ({len(bucket_clusters)} clusters)")
                except Exception:
                    logger.exception("Clustering LLM call failed for bucket %d", i + 1)

    # 6. Compute stats per cluster, expanding aliases into each cluster's keywords.
    keywords_map = {item["keyword"].lower(): item for item in approved}
    np_mod, vector_lookup = _load_keyword_vector_lookup(conn)
    alias_map_lower = {k.lower(): v for k, v in alias_map.items()}
    clusters: list[dict] = []
    by_primary: dict[str, dict] = {}
    for raw_cluster in raw_clusters:
        kw_list: list[str] = []
        seen: set[str] = set()
        for k in raw_cluster.get("keywords", []):
            kl = k.lower()
            if kl in keywords_map and kl not in seen:
                kw_list.append(k)
                seen.add(kl)
            for alias in alias_map_lower.get(kl, []):
                al = alias.lower()
                if al in keywords_map and al not in seen:
                    kw_list.append(alias)
                    seen.add(al)
        if not kw_list:
            continue
        cluster = {
            "name": raw_cluster.get("name", "Unnamed Cluster"),
            "content_type": raw_cluster.get("content_type", "blog_post"),
            "primary_keyword": raw_cluster.get("primary_keyword", kw_list[0]),
            "content_brief": raw_cluster.get("content_brief", ""),
            "keywords": kw_list,
        }
        cluster = _refresh_cluster_scoring(cluster, keywords_map, np_mod, vector_lookup)
        # Cross-bucket dedupe: same deterministic primary_keyword → keep higher-priority cluster.
        pk = (cluster["primary_keyword"] or "").strip().lower()
        if pk and pk in by_primary:
            existing = by_primary[pk]
            if cluster.get("priority_score", 0.0) > existing.get("priority_score", 0.0):
                clusters.remove(existing)
                clusters.append(cluster)
                by_primary[pk] = cluster
            continue
        if pk:
            by_primary[pk] = cluster
        clusters.append(cluster)

    # 6b. Post-process: merge cos-similar duplicates, then fold 1-keyword clusters.
    before = len(clusters)
    clusters = merge_similar_clusters(clusters, conn, keywords_map)
    clusters = fold_singletons(clusters, conn, keywords_map)
    clusters = [
        _refresh_cluster_scoring(c, keywords_map, np_mod, vector_lookup)
        for c in clusters
    ]
    if len(clusters) < before:
        progress(f"Post-processed clusters — {before} → {len(clusters)}")

    # 7. Sort by cluster priority: strong top keywords + demand + ranking upside.
    clusters.sort(key=lambda c: c.get("priority_score", 0), reverse=True)

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
    cluster_cols = {row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()}
    for cluster in clusters:
        sm = cluster.get("suggested_match")
        match_type = sm.get("match_type") if sm else None
        match_handle = sm.get("match_handle", "") if sm else None
        match_title = sm.get("match_title", "") if sm else None

        if "priority_score" in cluster_cols:
            conn.execute(
                """INSERT INTO clusters
                   (name, content_type, primary_keyword, content_brief,
                    total_volume, avg_difficulty, avg_opportunity, priority_score,
                    dominant_serp_features, content_format_hints, avg_cps,
                    match_type, match_handle, match_title, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cluster["name"],
                    cluster.get("content_type", "blog_post"),
                    cluster.get("primary_keyword", ""),
                    cluster.get("content_brief", ""),
                    cluster.get("total_volume", 0),
                    cluster.get("avg_difficulty", 0.0),
                    cluster.get("avg_opportunity", 0.0),
                    cluster.get("priority_score", 0.0),
                    (cluster.get("dominant_serp_features") or "").strip(),
                    (cluster.get("content_format_hints") or "").strip(),
                    float(cluster.get("avg_cps") or 0.0),
                    match_type,
                    match_handle,
                    match_title,
                    generated_at,
                ),
            )
        else:
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
