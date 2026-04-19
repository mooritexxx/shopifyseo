import json

from .dashboard_status import inspection_for_catalog_index_display

from .dashboard_detail_common import (
    badge,
    build_opportunity,
    escape_value,
    index_status_info,
    link,
    list_items,
    list_text_items,
    load_object_signals,
    has_search_console_inspect_link,
    parse_tags_json,
    render_manual_indexing_panel,
    search_console_inspect_href,
    trimmed_value,
    workflow_html,
    workflow_options,
)
from . import dashboard_queries as dq


def _text_value_block(value: str, empty_label: str) -> str:
    if value and value.strip():
        return f"<div class='editor-value'>{escape_value(value.strip())}</div>"
    return f"<div class='editor-value muted'>{escape_value(empty_label)}</div>"


def _html_value_block(value: str, empty_label: str) -> str:
    if value and value.strip():
        return f"<div class='prose-preview editor-prose'>{value}</div>"
    return f"<div class='editor-value muted'>{escape_value(empty_label)}</div>"


def _signal_metric(label: str, value: str, sublabel: str = "") -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    if isinstance(sublabel, list):
        sublabel = ", ".join(str(item) for item in sublabel)
    return (
        "<div class='signal-metric'>"
        f"<span>{escape_value(label)}</span>"
        f"<strong>{escape_value(value)}</strong>"
        f"{f'<small>{escape_value(sublabel)}</small>' if sublabel else ''}"
        "</div>"
    )


def _signal_card(label: str, value: str, sublabel: str, *, timestamp: str, handle: str, step: str) -> str:
    timestamp_text = str(timestamp or "never")
    return (
        "<div class='card signal-summary-card'>"
        "<div class='signal-summary-head'>"
        f"<span class='editor-label'>{escape_value(label)}</span>"
        f"<form class='signal-card-refresh-form' method='post' action='/api/refresh/product/{escape_value(handle)}' data-step='{escape_value(step)}' data-label='{escape_value(label)}'>"
        "<button type='submit' class='signal-refresh-button' aria-label='Refresh signal'>↻</button>"
        "</form>"
        "</div>"
        f"<strong>{escape_value(value)}</strong>"
        f"<small>{escape_value(sublabel)}</small>"
        f"<small class='signal-updated'>Updated {escape_value(timestamp_text)}</small>"
        "</div>"
    )


def render_product_detail(detail: dict, *, db_connect, verification: dict | None, workflow_statuses: list[str]) -> str:
    product = detail["product"]
    workflow = detail.get("workflow")
    conn = db_connect()
    try:
        signals = load_object_signals("product", product["handle"], conn=conn, gsc_period="mtd")
    finally:
        conn.close()
    recommendation_record = detail.get("recommendation") or {}
    recommendation_event = detail.get("recommendation_event") or {}
    recommendation = recommendation_record.get("details") or {}
    recommendation_history = detail.get("recommendation_history", [])
    opportunity = build_opportunity(
        dq.score_product(product)[0],
        dq.object_url("product", product["handle"]),
        recommendation.get("competitors", []),
        signals["gsc_detail"],
        signals["ga4_summary"],
        signals["inspection_detail"],
        product,
    )
    inspection_display = inspection_for_catalog_index_display(signals["inspection_detail"], product)
    index_label, index_kind, index_reason = index_status_info(inspection_display)

    current_tags = parse_tags_json(product["tags_json"])
    recommended_tags = recommendation.get("tags", "")
    current_body = product["description_html"] or ""
    recommended_body = recommendation.get("body", "")
    ai_status = recommendation_event.get("status") or "not_generated"
    ai_model = recommendation_record.get("model") or recommendation_event.get("model") or "Not generated"
    ai_timestamp = recommendation_record.get("created_at") or recommendation_event.get("created_at") or ""
    ai_error = recommendation_event.get("error_message") or ""
    ai_summary = recommendation_record.get("summary") or "No AI recommendation generated yet."
    ai_meta = recommendation.get("_meta") or {}
    ai_debug = recommendation.get("_debug") or {}
    product_url = dq.object_url("product", product["handle"])
    recommendation_history_items = [
        f"{badge((row.get('priority') or 'Medium'), (row.get('priority') or 'Medium').lower())} "
        f"{escape_value(row.get('created_at') or '')} · "
        f"{escape_value(row.get('source') or 'unknown')} · "
        f"{escape_value(row.get('status') or 'unknown')} · "
        f"{escape_value(row.get('summary') or '')}"
        for row in recommendation_history
    ]

    recommendation_history_html = list_items(recommendation_history_items, "No saved recommendation history.")
    opportunity_reasons = list_text_items(opportunity["reasons"], "No major opportunity drivers.")
    internal_links = list_text_items(recommendation.get("internal_links", []), "No internal link suggestions.")
    competitors = list_items(
        [f"{escape_value(row['competitor_name'])} ({escape_value(row['domain'])})" for row in recommendation.get("competitors", [])],
        "No matched competitors.",
    )
    collections = "".join(f"<li>{link(f'/collection/{row['handle']}', row['title'])}</li>" for row in detail["collections"]) or "<li>No collection memberships</li>"
    variants = "".join(
        f"<tr><td>{escape_value(row['title'])}</td><td>{escape_value(row['sku'] or '')}</td><td>{escape_value(row['price'] or '')}</td></tr>"
        for row in detail["variants"]
    ) or "<tr><td colspan='3'>No variants</td></tr>"
    metafields = "".join(
        f"<tr><td>{escape_value(row['namespace'])}</td><td>{escape_value(row['key'])}</td><td>{trimmed_value(row['value'] or '')}</td></tr>"
        for row in detail["metafields"]
    ) or "<tr><td colspan='3'>No metafields</td></tr>"

    body = f"""
<a class='back-link' href='/products'>Back to products</a>
<div class='detail-header compact'>
  <div>
    <div class='pill'>Product</div>
    <h1>{escape_value(product['title'])}</h1>
    <p><a href='{escape_value(product_url)}' target='_blank' rel='noreferrer'>Open live product</a></p>
  </div>
  <div class='actions'>
    <form method='post' action='/inspect/product/{escape_value(product["handle"])}' target='_blank'>
      <button type='submit'>Inspect URL</button>
    </form>
    <form class='ai-generate-form' method='post' action='/generate-ai/product/{escape_value(product["handle"])}' data-api-action='/api/generate-ai/product/{escape_value(product["handle"])}' data-object-label='{escape_value(product["title"])}'>
      <button type='submit' class='secondary'>Generate AI recommendations</button>
    </form>
    <form method='post' action='/refresh/product/{escape_value(product["handle"])}'>
      <button type='submit'>Refresh signals</button>
    </form>
  </div>
</div>
<div class='signal-summary-grid'>
  {_signal_card('Index', index_label, index_reason or 'No index detail', timestamp=product['index_last_fetched_at'] or '', handle=product['handle'], step='index')}
  {_signal_card('GSC clicks', str(int(product['gsc_clicks'] or 0)), 'Google Search', timestamp=product['gsc_last_fetched_at'] or '', handle=product['handle'], step='gsc_clicks')}
  {_signal_card('GSC impressions', str(int(product['gsc_impressions'] or 0)), 'Google Search', timestamp=product['gsc_last_fetched_at'] or '', handle=product['handle'], step='gsc_impressions')}
  {_signal_card('GSC CTR', f"{float(product['gsc_ctr'] or 0)*100:.2f}%", 'Click-through rate (search)', timestamp=product['gsc_last_fetched_at'] or '', handle=product['handle'], step='gsc_ctr')}
  {_signal_card('Avg. position (GSC)', f"{float(product['gsc_position'] or 0):.1f}", 'Average position in Search', timestamp=product['gsc_last_fetched_at'] or '', handle=product['handle'], step='gsc_position')}
  {_signal_card('GA4', f"{int(product['ga4_views'] or 0)} views", f"{int(product['ga4_sessions'] or 0)} sessions · {float(product['ga4_avg_session_duration'] or 0):.0f}s avg" if (product['ga4_views'] or product['ga4_sessions']) else 'No GA4 data', timestamp=product['ga4_last_fetched_at'] or '', handle=product['handle'], step='ga4')}
  {_signal_card('PageSpeed (mobile)', f"{int(product['pagespeed_performance'] or 0)} perf" if product['pagespeed_performance'] is not None else 'No score', product['pagespeed_status'] or 'Never fetched', timestamp=product['pagespeed_last_fetched_at'] or '', handle=product['handle'], step='speed')}
  {_signal_card('PageSpeed (desktop)', f"{int(product['pagespeed_desktop_performance'] or 0)} perf" if product.get('pagespeed_desktop_performance') is not None else 'No score', product.get('pagespeed_desktop_status') or 'Never fetched', timestamp=product.get('pagespeed_desktop_last_fetched_at') or '', handle=product['handle'], step='speed_desktop')}
</div>
<div class='detail-grid editor-layout full-width'>
  <div class='card editor-card'>
    <form method='post' action='/update/product/{escape_value(product["handle"])}' id='product-editor-form'>
      <div class='editor-toolbar'>
        <div>
          <h2>Editor</h2>
          <p>Review the current values, insert recommendations, and push the final draft to Shopify.</p>
          <div class='editor-ai-meta'>
            <span>{badge(ai_status.replace('_', ' ').title(), 'medium' if ai_status == 'success' else 'low')}</span>
            <span>{escape_value(ai_model)}</span>
            <span>{escape_value(ai_timestamp or 'No generation timestamp')}</span>
          </div>
          <p class='editor-ai-summary'>{escape_value(ai_summary)}</p>
          {f"<div class='message'>{escape_value(ai_error)}</div>" if ai_error else ""}
        </div>
        <div class='actions'>
          <button type='button' class='secondary' onclick='useAllProductRecommendations()'>Use all recommendations</button>
          <button type='button' class='secondary' onclick='resetProductDraft()'>Reset draft</button>
          <button type='submit'>Update Shopify</button>
        </div>
      </div>

      <div class='editor-section'>
        <div class='editor-section-head'>
          <div>
            <h3>SEO title</h3>
            <p>Primary click-through field for organic results.</p>
          </div>
          <button type='button' class='secondary' onclick='copyRecommendationValue("product-seo-title", "rec-product-seo-title")'>Use recommendation</button>
        </div>
        <div class='editor-compare-grid'>
          <div>
            <span class='editor-label'>Current</span>
            {_text_value_block(product['seo_title'] or '', 'No current SEO title')}
          </div>
          <div>
            <span class='editor-label'>Recommended</span>
            {_text_value_block(recommendation.get('seo_title', ''), 'No AI SEO title yet')}
          </div>
        </div>
        <label>Draft SEO title
          <input id='product-seo-title' type='text' name='seo_title' value='{escape_value(product["seo_title"] or "")}'>
        </label>
      </div>

      <div class='editor-section'>
        <div class='editor-section-head'>
          <div>
            <h3>SEO description</h3>
            <p>Use this to improve click-through rate and match the strongest intent.</p>
          </div>
          <button type='button' class='secondary' onclick='copyRecommendationValue("product-seo-description", "rec-product-seo-description")'>Use recommendation</button>
        </div>
        <div class='editor-compare-grid'>
          <div>
            <span class='editor-label'>Current</span>
            {_text_value_block(product['seo_description'] or '', 'No current SEO description')}
          </div>
          <div>
            <span class='editor-label'>Recommended</span>
            {_text_value_block(recommendation.get('seo_description', ''), 'No AI SEO description yet')}
          </div>
        </div>
        <label>Draft SEO description
          <textarea id='product-seo-description' name='seo_description'>{escape_value(product["seo_description"] or "")}</textarea>
        </label>
      </div>

      <div class='editor-section'>
        <div class='editor-section-head'>
          <div>
            <h3>Body HTML</h3>
            <p>Product copy should support model intent, flavor intent, and internal links.</p>
          </div>
          <button type='button' class='secondary' onclick='copyRecommendationValue("product-body-html", "rec-product-body-html")'>Use recommendation</button>
        </div>
        <div class='editor-compare-grid'>
          <div>
            <span class='editor-label'>Current</span>
            {_html_value_block(current_body, 'No current body copy')}
          </div>
          <div>
            <span class='editor-label'>Recommended</span>
            {_html_value_block(recommended_body, 'No AI body recommendation yet')}
          </div>
        </div>
        <label>Draft Body HTML
          <textarea id='product-body-html' name='body_html'>{escape_value(current_body)}</textarea>
        </label>
      </div>

      <div class='editor-section'>
        <div class='editor-section-head'>
          <div>
            <h3>Tags</h3>
            <p>Used for smarter collections, filtering, and merchandising support.</p>
          </div>
          <button type='button' class='secondary' onclick='copyRecommendationValue("product-tags", "rec-product-tags")'>Use recommendation</button>
        </div>
        <div class='editor-compare-grid'>
          <div>
            <span class='editor-label'>Current</span>
            {_text_value_block(current_tags, 'No current tags')}
          </div>
          <div>
            <span class='editor-label'>Recommended</span>
            {_text_value_block(recommended_tags, 'No AI tag recommendation yet')}
          </div>
        </div>
        <label>Draft tags
          <textarea id='product-tags' name='tags'>{escape_value(current_tags)}</textarea>
        </label>
      </div>

      <textarea id='orig-product-seo-title' class='editor-source' hidden>{escape_value(product["seo_title"] or "")}</textarea>
      <textarea id='orig-product-seo-description' class='editor-source' hidden>{escape_value(product["seo_description"] or "")}</textarea>
      <textarea id='orig-product-body-html' class='editor-source' hidden>{escape_value(current_body)}</textarea>
      <textarea id='orig-product-tags' class='editor-source' hidden>{escape_value(current_tags)}</textarea>
      <textarea id='rec-product-seo-title' class='editor-source' hidden>{escape_value(recommendation.get("seo_title", ""))}</textarea>
      <textarea id='rec-product-seo-description' class='editor-source' hidden>{escape_value(recommendation.get("seo_description", ""))}</textarea>
      <textarea id='rec-product-body-html' class='editor-source' hidden>{escape_value(recommended_body)}</textarea>
      <textarea id='rec-product-tags' class='editor-source' hidden>{escape_value(recommended_tags)}</textarea>
    </form>
  </div>
</div>

<details class='card detail-toggle' open>
  <summary>Internal links and competitor context</summary>
  <div class='detail-toggle-body'>
    <h3>Internal link suggestions</h3>
    <ul>{internal_links}</ul>
    <h3>Priority actions</h3>
    <ul>{list_text_items(recommendation.get('priority_actions', []), 'No priority actions yet.')}</ul>
    <h3>Why this matters</h3>
    <ul>{opportunity_reasons}</ul>
    <h3>Opportunity</h3>
    <p>{badge(opportunity['priority'], opportunity['priority'].lower())} Score {opportunity['score']}</p>
    {f"<div class='message'>{escape_value(verification.get('title') or '')}</div>" if verification and verification.get('ok') else ''}
    <h3>Matched competitors</h3>
    <ul>{competitors}</ul>
    <h3>Collection memberships</h3>
    <ul>{collections}</ul>
  </div>
</details>

<details class='card detail-toggle'>
  <summary>Recommendation history</summary>
  <div class='detail-toggle-body'>
    <ul>{recommendation_history_html}</ul>
  </div>
</details>

<details class='card detail-toggle'>
  <summary>AI prompt debug</summary>
  <div class='detail-toggle-body'>
    <h3>Condensed context</h3>
    <pre>{escape_value(json.dumps(ai_debug.get('condensed_context', {}), indent=2, ensure_ascii=False))}</pre>
    <h3>System prompt</h3>
    <pre>{escape_value(ai_debug.get('system_prompt', ''))}</pre>
    <h3>User prompt</h3>
    <pre>{escape_value(ai_debug.get('user_prompt', ''))}</pre>
  </div>
</details>

<details class='card detail-toggle'>
  <summary>Indexing</summary>
  <div class='detail-toggle-body'>
    {render_manual_indexing_panel(dq.object_url('product', product['handle']), inspection_display, workflow, signals['site_url'], f"/refresh/product/{product['handle']}")}
  </div>
</details>

<details class='card detail-toggle'>
  <summary>Workflow</summary>
  <div class='detail-toggle-body'>
    {workflow_html(workflow)}
    <form method='post' action='/workflow/product/{escape_value(product["handle"])}'>
      <label>Workflow status<select name='workflow_status'>{workflow_options(workflow_statuses, workflow)}</select></label>
      <label>Workflow notes<textarea name='workflow_notes'>{escape_value(workflow['notes'] if workflow else '')}</textarea></label>
      <div class='actions'><button type='submit'>Save Workflow</button></div>
    </form>
  </div>
</details>

<details class='card detail-toggle'>
  <summary>Advanced product data</summary>
  <div class='detail-toggle-body grid'>
    <div>
      <h3>Variants</h3>
      <table><thead><tr><th>Title</th><th>SKU</th><th>Price</th></tr></thead><tbody>{variants}</tbody></table>
    </div>
    <div>
      <h3>Metafields</h3>
      <table><thead><tr><th>Namespace</th><th>Key</th><th>Value</th></tr></thead><tbody>{metafields}</tbody></table>
    </div>
  </div>
</details>
"""
    return body
