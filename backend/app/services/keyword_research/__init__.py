"""keyword_research package — public API re-exports.

All public symbols are re-exported here so that existing imports of the form
    from backend.app.services.keyword_research import <symbol>
continue to work without modification.
"""

from .dataforseo_client import validate_dataforseo_access
from .google_ads_planner_metrics import refresh_google_ads_planner_metrics
from .competitor_blocklist import (
    COMPETITOR_BLOCKLIST_KEY,
    COMPETITOR_DISMISSED_SNAPSHOTS_KEY,
    DISCOVERY_SKIP_DOMAINS,
    add_competitor_to_blocklist,
    competitor_domain_allowed_for_research,
    load_competitor_blocklist,
    load_dismissed_snapshots,
    norm_competitor_domain,
    purge_disallowed_competitor_rows,
    remove_competitor_from_blocklist,
    upsert_dismissed_profile_snapshot,
)
from .keyword_db import (
    OPPORTUNITY_SCORING_VERSION,
    TARGET_KEY,
    bulk_update_status,
    cross_reference_gsc,
    load_approved_keywords,
    load_target_keywords,
    refresh_keyword_metric_opportunity_scores,
    refresh_opportunity_scores,
    sync_competitor_keyword_gaps,
    sync_competitor_profiles,
    sync_competitor_top_pages,
    sync_competitor_top_pages_from_keyword_metrics,
    sync_keyword_metrics_to_db,
    sync_keyword_page_map,
    update_competitor_profile_from_organic_keywords,
    update_keyword_status,
)
from .keyword_utils import (
    INTENT_PRIORITY,
    INTENT_TO_CONTENT,
    SERP_FORMAT_MAP,
    _merge_serp_features,
    batch_seeds,
    classify_intent,
    classify_ranking_status,
    compact_serp_features,
    compute_opportunity,
    deduplicate_results,
    derive_content_format_hint,
    match_gsc_queries,
    merge_with_existing,
    normalize_opportunity_scores,
    recompute_opportunity_scores,
)
from .research_runner import (
    COMPETITOR_DISCOVERY_PENDING_KEY,
    COMPETITOR_RESEARCH_META_KEY,
    _finalize_keyword_research,
    _preflight_keyword_research,
    _prepare_competitors_list,
    _run_source,
    load_competitor_discovery_pending,
    refresh_target_keyword_metrics,
    resolve_competitor_labs_target_domain,
    run_competitor_research,
    run_discover_competitors_for_review,
    run_research,
    run_seed_keyword_research,
    save_competitor_discovery_pending,
)

# Back-compat alias used by tests and older imports
_batch_seeds = batch_seeds

__all__ = [
    # dataforseo_client
    "validate_dataforseo_access",
    "refresh_google_ads_planner_metrics",
    # competitor_blocklist
    "COMPETITOR_BLOCKLIST_KEY",
    "COMPETITOR_DISMISSED_SNAPSHOTS_KEY",
    "DISCOVERY_SKIP_DOMAINS",
    "add_competitor_to_blocklist",
    "competitor_domain_allowed_for_research",
    "load_competitor_blocklist",
    "load_dismissed_snapshots",
    "norm_competitor_domain",
    "purge_disallowed_competitor_rows",
    "remove_competitor_from_blocklist",
    "upsert_dismissed_profile_snapshot",
    # keyword_db
    "TARGET_KEY",
    "OPPORTUNITY_SCORING_VERSION",
    "bulk_update_status",
    "cross_reference_gsc",
    "load_approved_keywords",
    "load_target_keywords",
    "refresh_keyword_metric_opportunity_scores",
    "refresh_opportunity_scores",
    "sync_competitor_keyword_gaps",
    "sync_competitor_profiles",
    "sync_competitor_top_pages",
    "sync_competitor_top_pages_from_keyword_metrics",
    "sync_keyword_metrics_to_db",
    "sync_keyword_page_map",
    "update_competitor_profile_from_organic_keywords",
    "update_keyword_status",
    # keyword_utils
    "INTENT_PRIORITY",
    "INTENT_TO_CONTENT",
    "SERP_FORMAT_MAP",
    "_merge_serp_features",
    "batch_seeds",
    "_batch_seeds",
    "classify_intent",
    "classify_ranking_status",
    "compact_serp_features",
    "compute_opportunity",
    "deduplicate_results",
    "derive_content_format_hint",
    "match_gsc_queries",
    "merge_with_existing",
    "normalize_opportunity_scores",
    "recompute_opportunity_scores",
    # research_runner
    "COMPETITOR_DISCOVERY_PENDING_KEY",
    "COMPETITOR_RESEARCH_META_KEY",
    "_finalize_keyword_research",
    "_preflight_keyword_research",
    "_prepare_competitors_list",
    "_run_source",
    "load_competitor_discovery_pending",
    "refresh_target_keyword_metrics",
    "resolve_competitor_labs_target_domain",
    "run_competitor_research",
    "run_discover_competitors_for_review",
    "run_research",
    "run_seed_keyword_research",
    "save_competitor_discovery_pending",
]
