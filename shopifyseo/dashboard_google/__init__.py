"""Google API integration for ShopifySEO.

This package replaces the former monolithic ``dashboard_google.py`` module.
All public symbols are re-exported from this ``__init__`` so that existing
import paths (``from shopifyseo.dashboard_google import X`` or
``from shopifyseo import dashboard_google as dg; dg.X``) continue to work
unchanged.

Sub-modules
-----------
_cache   Cache schema, TTL constants, SQLite read/write helpers.
_auth    OAuth flow, service tokens, settings, generic Google API helpers.
_gsc     Search Console analytics, URL inspection, PageSpeed Insights.
_ga4     Google Analytics 4 analytics and properties.
_ads     Google Ads API (KeywordPlanIdeaService, accessible customers).

Mutable module-level globals
-----------------------------
``GOOGLE_CLIENT_ID``, ``GOOGLE_CLIENT_SECRET``, ``GOOGLE_REDIRECT_URI``, and
``GOOGLE_AUTH_STATE`` are intentionally kept here (not in a sub-module)
because external code re-assigns them at runtime::

    dg.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    dg.GOOGLE_REDIRECT_URI = str(request.url_for("google_auth_callback"))

Sub-module functions that need these values call ``_pkg()`` → ``sys.modules``
at call time so they always see the current value without a circular import.
"""

import os

# ---------------------------------------------------------------------------
# Mutable globals (externally reassigned at runtime – must live here)
# ---------------------------------------------------------------------------

HOST = "127.0.0.1"
PORT = 8000
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = f"http://{HOST}:{PORT}/auth/google/callback"
GOOGLE_SCOPES = (
    "openid "
    "https://www.googleapis.com/auth/webmasters.readonly "
    "https://www.googleapis.com/auth/analytics.readonly "
    "https://www.googleapis.com/auth/adwords"
)
GOOGLE_ADS_API_VERSION = "v23"
GOOGLE_AUTH_STATE: dict = {"value": ""}

# In-memory caches shared across submodules (accessed via _pkg().GSC_CACHE)
GSC_CACHE: dict = {
    "summary": None,
    "per_url": {},
    "inspection": {},
    "pagespeed": {},
    "ga4": None,
    "ga4_per_url": {},
}

# ---------------------------------------------------------------------------
# Re-exports from _cache (constants + infrastructure)
# ---------------------------------------------------------------------------

from ._cache import (  # noqa: E402
    CACHE_TTLS,
    GSC_PROPERTY_BREAKDOWN_ROW_CAP,
    GSC_PROPERTY_BREAKDOWN_SPECS,
    GSC_QUERY_PAGE_ROW_CAP,
    _cache_meta,
    _get_cache_row,
    _load_cached_payload,
    _now_ts,
    _pct_delta,
    _pct_delta_float,
    _write_cache_payload,
    ensure_google_cache_schema,
)

# ---------------------------------------------------------------------------
# Re-exports from _auth
# ---------------------------------------------------------------------------

from ._auth import (  # noqa: E402
    get_google_access_token,
    get_service_setting,
    get_service_token,
    google_api_get,
    google_api_post,
    google_configured,
    google_exchange_code,
    google_refresh_token,
    google_token_has_scope,
    google_token_request,
    new_oauth_state,
    set_service_setting,
    set_service_token,
)

# ---------------------------------------------------------------------------
# Re-exports from _gsc
# ---------------------------------------------------------------------------

from ._gsc import (  # noqa: E402
    GSC_URL_QUERY_SECOND_DIMENSION_ROW_LIMIT,
    GSC_URL_QUERY_SECOND_DIMS,
    _overview_cache_key,
    _top_bucket_impressions_pct_vs_prior,
    clear_google_caches,
    delete_search_console_overview_cache,
    fetch_gsc_url_query_second_dimension,
    fetch_search_console_summary,
    get_gsc_property_breakdowns_cached,
    get_gsc_query_page_tables_cached,
    get_pagespeed,
    get_search_console_overview_cached,
    get_search_console_sites,
    get_search_console_summary_cached,
    get_search_console_url_detail,
    get_url_inspection,
    gsc_url_report_window,
    invalidate_pagespeed_memory_cache,
    preferred_site_url,
    refresh_gsc_property_breakdowns_for_site,
)

# ---------------------------------------------------------------------------
# Re-exports from _ga4
# ---------------------------------------------------------------------------

from ._ga4 import (  # noqa: E402
    delete_ga4_overview_cache,
    ga4_report_page_path_from_row,
    get_ga4_properties,
    get_ga4_property_overview_cached,
    get_ga4_summary,
    get_ga4_url_detail,
    ga4_url_cache_stale,
)

# ---------------------------------------------------------------------------
# Re-exports from _ads
# ---------------------------------------------------------------------------

from ._ads import (  # noqa: E402
    google_ads_keyword_plan_idea_post,
    google_ads_search,
    list_google_ads_accessible_customers,
    resolve_keyword_planning_customer,
    test_google_ads_api,
)
