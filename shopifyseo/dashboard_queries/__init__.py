"""Database query helpers used across the dashboard modules.

This package was split out of the original single ``dashboard_queries.py``
file so that thematically-related helpers live together and can be edited
in isolation. Public surface is unchanged: every name that used to be
importable from ``shopifyseo.dashboard_queries`` still is.

Submodules:

- :mod:`._text_tokens`   — HTML/token utilities + related-row scoring
- :mod:`._urls`          — storefront URLs + internal-link allowlist
- :mod:`._basic_fetchers`— ``fetch_all_*``, counts, overview, top organic
- :mod:`._seo_facts`     — SEO scoring + fact builders
- :mod:`._object_detail` — product / collection / page / article detail
- :mod:`._editors`       — editor write paths + workflow state upsert
- :mod:`._gsc_dimensions`— Tier B GSC dimensional rows + segment summary
"""
from __future__ import annotations

# Re-exports from sibling top-level modules (preserve historical import paths).
from ..dashboard_article_ideas import (  # noqa: F401
    bulk_delete_article_ideas,
    bulk_update_idea_status,
    compute_idea_performance,
    compute_keyword_coverage,
    delete_article_idea,
    fetch_article_idea_inputs,
    fetch_article_ideas,
    fetch_idea_articles,
    link_idea_to_article,
    refresh_article_idea_serp_snapshot,
    resolve_idea_targets,
    save_article_ideas,
    save_article_target_keywords,
    update_article_idea_status,
)
from ..dashboard_insights import blended_opportunity, opportunity_priority  # noqa: F401
from ..dashboard_status import index_status_info  # noqa: F401

# Submodule re-exports (preserve every name that used to live on this module).
from . import _urls as _urls_mod
from ._text_tokens import (  # noqa: F401
    _RELATED_STOPWORDS,
    _content_tokens_for_blog_article,
    _content_tokens_for_page,
    _product_overlap_score,
    _related_collections_by_token_overlap,
    _related_pages_by_token_overlap,
    _related_products_by_token_overlap,
    _strip_html_for_tokens,
    _tags_json_phrase_blob,
    _tokens_from_blob,
    blog_article_row_token_overlap,
    collection_row_token_overlap,
    product_row_token_overlap,
    retrieval_tokens_from_text,
    strip_html_for_retrieval,
    tags_json_phrase_for_retrieval,
)
from ._urls import (  # noqa: F401
    DEFAULT_INTERNAL_LINK_CAPS,
    _OBJECT_PATH_PREFIX,
    _base_store_url,
    blog_article_composite_handle,
    build_store_internal_link_allowlist,
    clear_base_url_cache,
    object_url,
    object_url_with_base,
)
from ._basic_fetchers import (  # noqa: F401
    _SEO_SIGNAL_TABLES,
    _row_factory,
    count_blog_articles_missing_meta,
    fetch_all_blog_articles,
    fetch_all_blog_articles_enriched,
    fetch_all_blogs,
    fetch_all_collections,
    fetch_all_pages,
    fetch_all_products,
    fetch_articles_by_blog_handle,
    fetch_blog_by_handle,
    fetch_counts,
    fetch_overview_metrics,
    fetch_recent_runs,
    fetch_top_organic_pages,
)
from ._seo_facts import (  # noqa: F401
    _seo_base_score,
    build_seo_fact,
    fetch_seo_facts,
)
from ._object_detail import (  # noqa: F401
    _fetch_recommendation,
    _fetch_recommendation_event,
    _fetch_recommendation_history,
    _fetch_workflow,
    _recommendation_row_to_dict,
    fetch_blog_article_detail,
    fetch_collection_detail,
    fetch_page_detail,
    fetch_product_detail,
)
from ._editors import (  # noqa: F401
    apply_saved_blog_article_fields_from_editor,
    apply_saved_collection_fields_from_editor,
    apply_saved_page_fields_from_editor,
    apply_saved_product_fields_from_editor,
    set_workflow_state,
)
from ._gsc_dimensions import (  # noqa: F401
    build_gsc_segment_summary_from_rows,
    fetch_gsc_query_dimension_rows,
    object_keys_with_dimensional_gsc,
)


def __getattr__(name: str):
    """Forward ``_BASE_URL_CACHE`` reads to the canonical home in :mod:`._urls`.

    The cache lives in ``_urls`` after the package split. To invalidate it,
    callers should use :func:`clear_base_url_cache`.
    """
    if name == "_BASE_URL_CACHE":
        return _urls_mod._BASE_URL_CACHE
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
