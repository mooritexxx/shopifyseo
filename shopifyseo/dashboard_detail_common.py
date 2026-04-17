import html
import json
import sqlite3
from textwrap import shorten
from urllib.parse import quote, urlparse

from .dashboard_insights import blended_opportunity
from .dashboard_http import HttpRequestError
from . import dashboard_google as dg
from . import dashboard_queries as dq
from .dashboard_status import (
    cache_status_kind,
    cache_status_label,
    cache_status_text,
    escape_value,
    index_status_info,
)


def badge(label: str, kind: str = "default") -> str:
    kind_class = {
        "high": "badge-high",
        "medium": "badge-medium",
        "low": "badge-low",
        "default": "badge-default",
    }.get(kind, "badge-default")
    return f"<span class='badge {kind_class}'>{html.escape(label)}</span>"


def link(path: str, label: str) -> str:
    return f"<a href='{html.escape(path)}'>{html.escape(label)}</a>"


def search_console_inspect_href(url: str, site_url: str, inspection_detail: dict | None) -> str:
    inspection_link = ((inspection_detail or {}).get("inspectionResult") or {}).get("inspectionResultLink")
    if inspection_link:
        return inspection_link
    if site_url:
        return f"https://search.google.com/search-console?resource_id={quote(site_url, safe='')}"
    return "https://search.google.com/search-console"


def has_search_console_inspect_link(inspection_detail: dict | None) -> bool:
    return bool(((inspection_detail or {}).get("inspectionResult") or {}).get("inspectionResultLink"))


def render_manual_indexing_panel(
    url: str,
    inspection_detail: dict | None,
    workflow,
    site_url: str,
    refresh_path: str,
) -> str:
    inspection_meta = (inspection_detail or {}).get("_cache")
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    coverage = idx.get("coverageState", "") or "No coverage data"
    indexing = idx.get("indexingState", "") or "No indexing data"
    canonical = idx.get("googleCanonical", "") or "No canonical reported"
    index_label, index_kind, index_reason = index_status_info(inspection_detail)
    sitemap = idx.get("referringUrlsFromSitemap", []) or idx.get("sitemap", []) or []
    sitemap_html = (
        "<ul>" + "".join(f"<li>{escape_value(item)}</li>" for item in sitemap[:5]) + "</ul>"
        if sitemap
        else "<p>No referring sitemap reported in the current inspection response.</p>"
    )
    workflow_status = workflow["status"] if workflow else "Needs fix"
    preflight = [
        "Confirm the URL is live and returns the correct canonical.",
        "Make sure the title, meta description, and body copy are complete.",
        "Check that the page is linked from relevant products, collections, or brand pages.",
        "Verify the URL is included in the sitemap and not blocked by robots or noindex.",
    ]
    property_href = (
        f"https://search.google.com/search-console?resource_id={quote(site_url, safe='')}"
        if site_url
        else "https://search.google.com/search-console"
    )
    inspect_href = search_console_inspect_href(url, site_url, inspection_detail)
    inspect_action = (
        f"<a href='{escape_value(inspect_href)}' target='_blank' rel='noreferrer'>Inspect URL</a>"
        if has_search_console_inspect_link(inspection_detail)
        else ""
    )
    checklist_html = "".join(f"<li>{escape_value(item)}</li>" for item in preflight)
    return f"""
    <h2>Manual Indexing</h2>
    <p>{badge(index_label, index_kind)} {escape_value(index_reason)}</p>
    <p>{badge(cache_status_label(inspection_meta), cache_status_kind(inspection_meta))} {escape_value(cache_status_text(inspection_meta))}</p>
    <p><strong>Status:</strong> {escape_value(workflow_status)}</p>
    <p><strong>Coverage:</strong> {escape_value(coverage)}</p>
    <p><strong>Indexing:</strong> {escape_value(indexing)}</p>
    <p><strong>Google canonical:</strong> {escape_value(canonical)}</p>
    <h3>Preflight Checklist</h3>
    <ul>{checklist_html}</ul>
    <h3>Sitemap Signals</h3>
    {sitemap_html}
    <div class='actions'>
      <a href='{escape_value(url)}' target='_blank' rel='noreferrer'>Open Live URL</a>
      {inspect_action}
      <a href='{property_href}' target='_blank' rel='noreferrer'>Open Search Console</a>
      <button type='button' class='secondary' onclick="navigator.clipboard.writeText('{html.escape(url, quote=True)}'); this.textContent='Copied URL';">Copy URL</button>
    </div>
    <form method='post' action='{escape_value(refresh_path)}'>
      <div class='actions'><button type='submit'>Refresh Index Status</button></div>
    </form>
    """


def load_object_signals(kind: str, handle: str, *, conn: sqlite3.Connection, gsc_period: str = "mtd"):
    site_url = ""
    gsc_detail = None
    inspection_detail = None
    pagespeed_detail = None
    ga4_summary = None
    errors = {}
    site_url = dg.get_service_setting(conn, "search_console_site")
    url = dq.object_url(kind, handle)
    try:
        gsc_detail = dg.get_search_console_url_detail(
            conn, url, refresh=False, object_type=kind, object_handle=handle, gsc_period=gsc_period
        )
    except (RuntimeError, HttpRequestError, KeyError, ValueError):
        errors["gsc"] = "GSC detail unavailable"
    try:
        inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=kind, object_handle=handle)
    except (RuntimeError, HttpRequestError, KeyError, ValueError):
        errors["index"] = "URL inspection unavailable"
    try:
        ga4_summary = dg.get_ga4_summary(conn, refresh=False)
    except (RuntimeError, HttpRequestError, KeyError, ValueError):
        errors["ga4"] = "GA4 summary unavailable"
    try:
        pagespeed_detail = dg.get_pagespeed(conn, url, refresh=False, object_type=kind, object_handle=handle)
    except (RuntimeError, HttpRequestError, KeyError, ValueError):
        errors["pagespeed"] = "PageSpeed unavailable"
    return {
        "site_url": site_url,
        "gsc_detail": gsc_detail,
        "inspection_detail": inspection_detail,
        "ga4_summary": ga4_summary,
        "pagespeed_detail": pagespeed_detail,
        "errors": errors,
    }


def render_verification_html(verification: dict | None) -> str:
    if not verification:
        return ""
    return (
        f"<h2>Live Verification</h2><p><strong>URL:</strong> {escape_value(verification['url'])}</p>"
        f"<p><strong>Title:</strong> {escape_value(verification.get('title', ''))}</p>"
        f"<p><strong>Description:</strong> {escape_value(verification.get('description', ''))}</p>"
    )


def render_gsc_html(gsc_detail: dict | None) -> str:
    if not (gsc_detail and gsc_detail.get("page_rows")):
        return ""
    row = gsc_detail["page_rows"][0]
    queries = "".join(
        f"<li>{escape_value((q.get('keys') or [''])[0])} · {int(q.get('clicks',0))} clicks · {int(q.get('impressions',0))} impressions · {q.get('position',0):.1f} pos</li>"
        for q in gsc_detail["query_rows"][:8]
    ) or "<li>No query data.</li>"
    return (
        f"<h2>Search Console</h2><p><strong>Clicks:</strong> {int(row.get('clicks',0))} · "
        f"<strong>Impressions:</strong> {int(row.get('impressions',0))} · "
        f"<strong>CTR:</strong> {row.get('ctr',0)*100:.1f}% · "
        f"<strong>Position:</strong> {row.get('position',0):.1f}</p><ul>{queries}</ul>"
    )


def render_inspection_html(inspection_detail: dict | None) -> str:
    if not inspection_detail:
        return ""
    idx = inspection_detail.get("inspectionResult", {}).get("indexStatusResult", {})
    return (
        f"<h2>URL Inspection</h2><p><strong>Coverage:</strong> {escape_value(idx.get('coverageState',''))}</p>"
        f"<p><strong>Indexing:</strong> {escape_value(idx.get('indexingState',''))}</p>"
        f"<p><strong>Google canonical:</strong> {escape_value(idx.get('googleCanonical',''))}</p>"
    )


def inspection_for_display(inspection_detail: dict | None, summary_row=None) -> dict | None:
    if inspection_detail:
        return inspection_detail
    if not summary_row:
        return None
    if not (summary_row["index_status"] or summary_row["index_coverage"] or summary_row["google_canonical"]):
        return None
    return {
        "inspectionResult": {
            "indexStatusResult": {
                "indexingState": summary_row["index_status"] or "",
                "coverageState": summary_row["index_coverage"] or "",
                "googleCanonical": summary_row["google_canonical"] or "",
            }
        }
    }


def render_pagespeed_html(pagespeed_detail: dict | None) -> str:
    if not pagespeed_detail:
        return ""
    cats = pagespeed_detail.get("lighthouseResult", {}).get("categories", {})
    perf = cats.get("performance", {}).get("score")
    seo = cats.get("seo", {}).get("score")
    if perf is None and seo is None:
        return ""
    return (
        f"<h2>PageSpeed</h2><p><strong>Performance:</strong> {int((perf or 0)*100)}</p>"
        f"<p><strong>SEO:</strong> {int((seo or 0)*100)}</p>"
    )


def build_opportunity(
    base_score: int,
    url: str,
    competitors: list[dict],
    gsc_detail: dict | None,
    ga4_summary: dict | None,
    inspection_detail: dict | None,
    summary_row=None,
) -> dict:
    gsc_summary = {"pages": gsc_detail.get("page_rows", []), "queries": gsc_detail.get("query_rows", [])} if gsc_detail else None
    if not gsc_summary and summary_row and ((summary_row["gsc_impressions"] or 0) or (summary_row["gsc_clicks"] or 0)):
        gsc_summary = {
            "pages": [
                {
                    "keys": [url],
                    "clicks": int(summary_row["gsc_clicks"] or 0),
                    "impressions": int(summary_row["gsc_impressions"] or 0),
                    "ctr": float(summary_row["gsc_ctr"] or 0),
                    "position": float(summary_row["gsc_position"] or 0),
                }
            ],
            "queries": [],
        }
    ga4_summary_payload = ga4_summary
    if not ga4_summary_payload and summary_row and ((summary_row["ga4_sessions"] or 0) or (summary_row["ga4_views"] or 0)):
        ga4_summary_payload = {
            "rows": [
                {
                    "dimensionValues": [{"value": urlparse(url or "").path or "/"}],
                    "metricValues": [
                        {"value": str(int(summary_row["ga4_sessions"] or 0))},
                        {"value": str(int(summary_row["ga4_views"] or 0))},
                        {"value": str(float(summary_row["ga4_avg_session_duration"] or 0))},
                    ],
                }
            ]
        }
    inspection_payload = inspection_detail
    if not inspection_payload and summary_row and (summary_row["index_status"] or summary_row["index_coverage"] or summary_row["google_canonical"]):
        inspection_payload = {
            "inspectionResult": {
                "indexStatusResult": {
                    "indexingState": summary_row["index_status"] or "",
                    "coverageState": summary_row["index_coverage"] or "",
                    "googleCanonical": summary_row["google_canonical"] or "",
                }
            }
        }
    return blended_opportunity(
        base_score=base_score,
        url=url,
        competitors=competitors,
        gsc_summary=gsc_summary,
        ga4_summary=ga4_summary_payload,
        inspection=inspection_payload,
    )


def render_ga4_html(opportunity: dict) -> str:
    if not opportunity.get("ga4_row"):
        return ""
    metrics = opportunity["ga4_row"].get("metricValues", [])
    sessions = int(float(metrics[0].get("value", 0))) if len(metrics) > 0 else 0
    views = int(float(metrics[1].get("value", 0))) if len(metrics) > 1 else 0
    avg_duration = float(metrics[2].get("value", 0)) if len(metrics) > 2 else 0.0
    return (
        f"<h2>GA4</h2><p><strong>Sessions:</strong> {sessions} · "
        f"<strong>Views:</strong> {views} · "
        f"<strong>Avg duration:</strong> {avg_duration:.0f}s</p>"
    )


def render_google_signals_block(
    kind: str,
    handle: str,
    gsc_detail: dict | None,
    inspection_detail: dict | None,
    pagespeed_detail: dict | None,
    ga4_summary: dict | None,
    verification_html: str,
    provider_errors: dict | None = None,
) -> str:
    gsc_meta = gsc_detail.get("_cache") if gsc_detail else None
    inspection_meta = inspection_detail.get("_cache") if inspection_detail else None
    pagespeed_meta = pagespeed_detail.get("_cache") if pagespeed_detail else None
    ga4_meta = ga4_summary.get("_cache") if ga4_summary else None
    provider_error_html = ""
    if provider_errors:
        provider_error_html = "<ul>" + "".join(f"<li>{escape_value(message)}</li>" for message in provider_errors.values()) + "</ul>"
    return f"""
    <h2>Google Signals</h2>
    <p>{badge(cache_status_label(gsc_meta), cache_status_kind(gsc_meta))} GSC detail · {escape_value(cache_status_text(gsc_meta))}</p>
    <p>{badge(cache_status_label(inspection_meta), cache_status_kind(inspection_meta))} URL inspection · {escape_value(cache_status_text(inspection_meta))}</p>
    <p>{badge(cache_status_label(pagespeed_meta), cache_status_kind(pagespeed_meta))} PageSpeed · {escape_value(cache_status_text(pagespeed_meta))}</p>
    <p>{badge(cache_status_label(ga4_meta), cache_status_kind(ga4_meta))} GA4 summary · {escape_value(cache_status_text(ga4_meta))}</p>
    {provider_error_html}
    {render_gsc_html(gsc_detail)}
    {render_inspection_html(inspection_detail)}
    {render_pagespeed_html(pagespeed_detail)}
    {verification_html}
    <form method='post' action='/refresh/{escape_value(kind)}/{escape_value(handle)}'>
      <div class='actions'><button type='submit'>Refresh Google Signals</button></div>
    </form>
    """


def workflow_options(workflow_statuses: list[str], workflow) -> str:
    return "".join(
        f"<option value='{escape_value(status)}' {'selected' if workflow and workflow['status']==status else ''}>{escape_value(status)}</option>"
        for status in workflow_statuses
    )


def workflow_html(workflow) -> str:
    if workflow:
        return f"<p><strong>Workflow:</strong> {escape_value(workflow['status'])}</p><p>{escape_value(workflow['notes'] or '')}</p>"
    return "<p><strong>Workflow:</strong> Needs fix</p>"


def list_items(items: list[str], empty: str) -> str:
    return "".join(f"<li>{item}</li>" for item in items) or f"<li>{escape_value(empty)}</li>"


def list_text_items(items: list[str], empty: str) -> str:
    return "".join(f"<li>{escape_value(item)}</li>" for item in items) or f"<li>{escape_value(empty)}</li>"


def trimmed_value(value: str, width: int = 90) -> str:
    return escape_value(shorten(value or "", width=width, placeholder="..."))


def parse_tags_json(tags_json: str) -> str:
    return ", ".join(json.loads(tags_json)) if tags_json else ""


def render_current_vs_recommended(current_title: str, current_description: str, current_body: str, recommendation: dict) -> str:
    def text_block(value: str, empty_label: str) -> str:
        if value and value.strip():
            return f"<div class='compare-block'>{escape_value(value.strip())}</div>"
        return f"<div class='compare-block muted'>{escape_value(empty_label)}</div>"

    def prose_block(value: str, empty_label: str) -> str:
        if value and value.strip():
            return f"<div class='prose-preview compare-prose'>{value}</div>"
        return f"<div class='compare-block muted'>{escape_value(empty_label)}</div>"

    recommended_body = recommendation.get("body") or ""
    return f"""
    <div class='compare-grid'>
      <div class='card compare-card'>
        <h3>Current</h3>
        <p><strong>SEO title</strong></p>
        {text_block(current_title, 'No current SEO title')}
        <p><strong>SEO description</strong></p>
        {text_block(current_description, 'No current SEO description')}
        <p><strong>Body</strong></p>
        {prose_block(current_body, 'No current body copy')}
      </div>
      <div class='card compare-card'>
        <h3>Recommended</h3>
        <p><strong>SEO title</strong></p>
        {text_block(recommendation.get("seo_title", ""), 'No recommended SEO title')}
        <p><strong>SEO description</strong></p>
        {text_block(recommendation.get("seo_description", ""), 'No recommended SEO description')}
        <p><strong>Body direction</strong></p>
        {prose_block(recommended_body, 'No recommended body direction')}
      </div>
    </div>
    """
