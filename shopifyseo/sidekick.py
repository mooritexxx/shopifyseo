"""ShopifySEO Sidekick — in-app chat for product/collection/page/article detail views (one turn + optional field_updates)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import dashboard_queries as dq
from .dashboard_ai_engine_parts.generation import (
    AIProviderRequestError,
    _call_ai,
    _require_provider_credentials,
    ai_settings,
)

SIDEKICK_RESPONSE_SCHEMA = {
    "name": "sidekick_response",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "Helpful answer, Markdown allowed. Explain SEO rationale when editing.",
            },
            "field_updates": {
                "type": "object",
                "description": "Only keys the user asked to change; use empty object if no edits.",
                "properties": {
                    "title": {"type": "string"},
                    "seo_title": {"type": "string"},
                    "seo_description": {"type": "string"},
                    "body_html": {"type": "string"},
                    "tags": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["reply", "field_updates"],
        "additionalProperties": False,
    },
}

_ALLOWED_BY_RESOURCE = {
    "product": {"title", "seo_title", "seo_description", "body_html", "tags"},
    "collection": {"title", "seo_title", "seo_description", "body_html"},
    "page": {"title", "seo_title", "seo_description", "body_html"},
    "blog_article": {"title", "seo_title", "seo_description", "body_html"},
}

_BODY_MAX_CONTEXT = 12_000

_LINK_TYPES = frozenset({"product", "collection", "page"})


def _approved_internal_link_targets_for_sidekick(resource_type: str, detail: dict[str, Any]) -> list[dict[str, str]]:
    """Ground Sidekick on real storefront URLs so it cannot invent internal links."""
    rows: list[tuple[str, dict[str, Any]]] = []
    if resource_type == "product":
        for row in detail.get("collections") or []:
            if isinstance(row, dict) and row.get("handle"):
                rows.append(("collection", row))
    else:
        for row in detail.get("related_items") or []:
            if isinstance(row, dict):
                kind = str(row.get("type") or "").strip().lower()
                if kind in _LINK_TYPES and row.get("handle"):
                    rows.append((kind, row))

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for kind, row in rows:
        handle = str(row.get("handle") or "").strip()
        if not handle:
            continue
        url = dq.object_url(kind, handle)
        if url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "type": kind,
                "handle": handle,
                "title": str(row.get("title") or "").strip(),
                "url": url,
            }
        )
    return out


def _truncate(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 20] + "\n…[truncated]…"


def _compact_json(data: Any, max_len: int = 6000) -> str:
    raw = json.dumps(data, ensure_ascii=True, default=str)
    return _truncate(raw, max_len)


def build_sidekick_context_block(
    *,
    resource_type: str,
    handle: str,
    detail: dict[str, Any],
    client_draft: dict[str, str] | None,
    conn: sqlite3.Connection | None = None,
    user_message: str = "",
) -> str:
    """System prompt body: DB detail + optional live form values + RAG context."""
    approved = _approved_internal_link_targets_for_sidekick(resource_type, detail)
    market_block = ""
    if conn is not None:
        try:
            from shopifyseo.market_context import build_market_prompt_fragment
            market_block = build_market_prompt_fragment(conn)
        except Exception:
            pass

    lines = [
        "You are Sidekick, an in-app SEO assistant for Shopify catalog editors.",
        f"Context: {resource_type} with handle={handle!r}.",
        "The user may ask questions or request rewrites. When they want concrete text changes, fill field_updates with only the fields that should change.",
        "Respect Shopify SEO title (~60 chars) and meta description (~150–160 chars) conventions when applicable.",
        "",
    ]
    if market_block:
        lines.extend([
            "### Primary market",
            market_block,
            "",
        ])
    lines.extend([
        "### Internal links (mandatory when editing body_html)",
        "If you add or change `<a href>` in body_html, EVERY href must be character-for-character identical to a `url` listed under "
        "`approved_internal_link_targets` below. Do not invent /collections/, /products/, or /pages/ paths or handles. "
        "If no listed URL fits the user's request, explain that in `reply` and leave links unchanged (or omit new links).",
        "",
        "### approved_internal_link_targets (only these storefront URLs exist for this object)",
        _compact_json(approved, 8000) if approved else "[]  (empty — do not add in-body internal links unless the user provides a full URL you can verify is intentional)",
        "",
    ])
    if client_draft:
        lines.append("### Current form (may include unsaved edits)")
        lines.append(_compact_json(client_draft, 8000))
        lines.append("")
    gs = detail.get("gsc_segment_summary")
    if isinstance(gs, dict) and (
        gs.get("device_mix") or gs.get("top_countries") or gs.get("search_appearances") or gs.get("top_pairs")
    ):
        slim_gs = {
            "device_mix": (gs.get("device_mix") or [])[:5],
            "top_countries": (gs.get("top_countries") or [])[:5],
            "search_appearances": (gs.get("search_appearances") or [])[:5],
            "top_pairs": (gs.get("top_pairs") or [])[:8],
            "fetched_at": gs.get("fetched_at"),
        }
        lines.append("### gsc_segment_summary (Search Console, Overview-aligned window, cached)")
        lines.append(
            "Use only these buckets for device/country/SERP-appearance claims; do not invent segment data."
        )
        lines.append(_compact_json(slim_gs, 2000))
        lines.append("")
    seo_gaps = detail.get("seo_keyword_gaps")
    if isinstance(seo_gaps, dict) and seo_gaps.get("must_consider"):
        lines.append("### seo_keyword_gaps (cluster keywords NOT yet covered on this page)")
        lines.append(
            "These high-priority keywords are missing from the current content, sorted by opportunity. "
            "When suggesting edits, weave must_consider keywords naturally: 1–2 in seo_title, 2–3 in seo_description, "
            "as many as natural in body. already_present keywords are already covered — do not repeat unnecessarily. "
            "Readability and conversion always take priority over keyword density."
        )
        lines.append(_compact_json(seo_gaps, 3000))
        lines.append("")
    if resource_type == "product":
        draft = detail.get("draft") or {}
        rec = (detail.get("recommendation") or {}).get("details") or {}
        opp = detail.get("opportunity") or {}
        lines.append("### Saved catalog / draft (from database)")
        lines.append(
            _compact_json(
                {
                    "title": draft.get("title"),
                    "seo_title": draft.get("seo_title"),
                    "seo_description": draft.get("seo_description"),
                    "tags": draft.get("tags"),
                    "body_excerpt": _truncate(draft.get("body_html") or "", _BODY_MAX_CONTEXT),
                    "recommendation_status": (detail.get("recommendation") or {}).get("status"),
                    "ai_recommendation_excerpt": _compact_json(
                        {k: rec.get(k) for k in ("seo_title", "seo_description", "body", "tags") if rec.get(k)},
                        4000,
                    ),
                    "opportunity": {"score": opp.get("score"), "priority": opp.get("priority")},
                },
                12000,
            )
        )
    else:
        draft = detail.get("draft") or {}
        rec = (detail.get("recommendation") or {}).get("details") or {}
        lines.append("### Saved content (from database)")
        lines.append(
            _compact_json(
                {
                    "title": draft.get("title"),
                    "seo_title": draft.get("seo_title"),
                    "seo_description": draft.get("seo_description"),
                    "body_excerpt": _truncate(draft.get("body_html") or "", _BODY_MAX_CONTEXT),
                    "recommendation_status": (detail.get("recommendation") or {}).get("status"),
                    "ai_recommendation_excerpt": _compact_json(
                        {k: rec.get(k) for k in ("seo_title", "seo_description", "body") if rec.get(k)},
                        4000,
                    ),
                    "opportunity": (detail.get("opportunity") or {}),
                },
                12000,
            )
        )
    if conn is not None and user_message:
        try:
            from .embedding_store import retrieve_for_sidekick
            from .dashboard_ai_engine_parts.settings import ai_settings
            settings = ai_settings(conn)
            api_key = (settings.get("gemini_api_key") or "").strip()
            if api_key:
                obj_title = (detail.get("draft") or {}).get("title") or handle
                related = retrieve_for_sidekick(
                    conn, api_key, user_message, resource_type, handle,
                    object_title=obj_title, top_k=3,
                )
                if related:
                    lines.append("### Related content in your store (from semantic search)")
                    for r in related:
                        lines.append(
                            f"- [{r['object_type']}] {r['object_handle']} — "
                            f"{(r.get('source_text_preview') or '')[:120]}"
                        )
                    lines.append("")
        except Exception:
            pass

    return "\n".join(lines)


def _sanitize_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages[-24:]:
        role = (m.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        content = content[:12000]
        out.append({"role": role, "content": content})
    return out


def run_sidekick_turn(
    conn: sqlite3.Connection,
    *,
    resource_type: str,
    handle: str,
    detail: dict[str, Any],
    messages: list[dict[str, str]],
    client_draft: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return { reply, field_updates } after one model call."""
    settings = ai_settings(conn)
    provider = settings["sidekick_provider"]
    model = settings["sidekick_model"]
    _require_provider_credentials(settings, provider)
    timeout = settings["timeout"]

    last_user_msg = ""
    for m in reversed(messages):
        if (m.get("role") or "").strip().lower() == "user":
            last_user_msg = str(m.get("content") or "").strip()
            break
    system = build_sidekick_context_block(
        resource_type=resource_type,
        handle=handle,
        detail=detail,
        client_draft=client_draft,
        conn=conn,
        user_message=last_user_msg,
    )
    chat_messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    chat_messages.extend(_sanitize_messages(messages))

    try:
        raw = _call_ai(
            settings,
            provider,
            model,
            chat_messages,
            timeout,
            json_schema=SIDEKICK_RESPONSE_SCHEMA,
            stage="sidekick",
        )
    except AIProviderRequestError as exc:
        raise RuntimeError(str(exc)) from exc

    reply = str(raw.get("reply") or "").strip()
    updates_in = raw.get("field_updates")
    allowed = _ALLOWED_BY_RESOURCE.get(resource_type, set())
    field_updates: dict[str, str] = {}
    if isinstance(updates_in, dict):
        for key, val in updates_in.items():
            if key not in allowed:
                continue
            if not isinstance(val, str):
                continue
            v = val.strip()
            if v:
                field_updates[key] = v

    if not reply and field_updates:
        reply = "Here are the updated fields — review and apply when ready."

    if not reply:
        reply = "I could not produce a response. Try rephrasing your question."

    return {"reply": reply, "field_updates": field_updates}
