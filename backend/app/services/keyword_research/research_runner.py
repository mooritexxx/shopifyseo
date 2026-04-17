"""Research orchestration — seed and competitor pipelines (DataForSEO Labs)."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

from shopifyseo.dashboard_google import get_service_setting, set_service_setting

from .dataforseo_client import (
    call_bulk_traffic_estimation,
    call_competitors_domain,
    call_google_autocomplete_suggestions,
    call_keyword_ideas,
    call_keyword_overview,
    call_keyword_suggestions,
    call_ranked_keywords,
    call_relevant_pages,
    validate_dataforseo_access,
)
from .competitor_blocklist import (
    competitor_domain_allowed_for_research,
    norm_competitor_domain,
    purge_disallowed_competitor_rows,
)
from .keyword_db import (
    TARGET_KEY,
    apply_competitor_traffic_from_provider_batch,
    sync_competitor_keyword_gaps,
    sync_competitor_profiles,
    sync_competitor_top_pages,
    sync_keyword_metrics_to_db,
    sync_keyword_page_map,
    update_competitor_profile_organic_sample_count,
)
from .keyword_db import load_target_keywords
from .keyword_utils import (
    batch_seeds,
    classify_intent,
    compute_opportunity,
    deduplicate_results,
    derive_content_format_hint,
    merge_with_existing,
    normalize_opportunity_scores,
)

logger = logging.getLogger(__name__)

COMPETITOR_RESEARCH_META_KEY = "competitor_research_meta"


def _run_source(
    label: str,
    call_fn,
    call_args: list,
    batch_label: str,
    all_raw: list[dict],
    errors: list[str],
    on_progress=None,
) -> float:
    """Run a single source, appending results to all_raw. Returns cost."""
    cost = 0.0
    for i, args in enumerate(call_args):
        if on_progress:
            on_progress(f"{label} ({i + 1}/{len(call_args)}): {batch_label(args)}")
        try:
            items, c = call_fn(*args)
            seed_key = args[-1] if isinstance(args[-1], str) else args[1]
            for item in items:
                item["seed_keywords"] = {seed_key} if isinstance(seed_key, str) else set(seed_key)
            all_raw.extend(items)
            cost += float(c or 0)
        except RuntimeError as exc:
            logger.warning("Skipping %s batch due to error: %s", label, exc)
            errors.append(str(exc))
    return cost


def _primary_country_iso(conn: sqlite3.Connection) -> str:
    from shopifyseo.market_context import get_primary_country_code

    raw = (get_primary_country_code(conn) or "CA").strip().upper()
    return raw if len(raw) == 2 else "CA"


def _resolve_dataforseo_credentials(conn: sqlite3.Connection) -> tuple[str, str]:
    login = (get_service_setting(conn, "dataforseo_api_login") or "").strip()
    password = (get_service_setting(conn, "dataforseo_api_password") or "").strip()
    if not login or not password:
        raise RuntimeError(
            "Configure keyword research: set DataForSEO API login and password in Settings > Integrations."
        )
    return login, password


def _preflight_keyword_research(conn: sqlite3.Connection, on_progress) -> tuple[str, str]:
    """Validate DataForSEO access; return (login, password)."""
    login, password = _resolve_dataforseo_credentials(conn)
    if on_progress:
        on_progress("Validating DataForSEO API access…")
    err = validate_dataforseo_access(login, password, country_iso=_primary_country_iso(conn))
    if err:
        raise RuntimeError(err)
    return login, password


def _prepare_competitors_list(conn: sqlite3.Connection) -> list[str]:
    """Load competitor_domains from settings: normalize, dedupe, drop blocklist / junk domains, purge rows."""
    competitor_raw = get_service_setting(conn, "competitor_domains", "[]")
    try:
        competitors_raw_list = json.loads(competitor_raw)
    except json.JSONDecodeError:
        competitors_raw_list = []
    seen_norm: set[str] = set()
    competitors_full_norm: list[str] = []
    for d in competitors_raw_list:
        n = norm_competitor_domain(str(d))
        if not n or n in seen_norm:
            continue
        seen_norm.add(n)
        competitors_full_norm.append(n)
    competitors = [n for n in competitors_full_norm if competitor_domain_allowed_for_research(conn, n)]
    if competitors != competitors_full_norm:
        set_service_setting(conn, "competitor_domains", json.dumps(competitors))
    try:
        purge_disallowed_competitor_rows(conn)
    except Exception:
        logger.exception("purge_disallowed_competitor_rows failed (non-fatal)")
    return competitors


def _finalize_keyword_research(
    conn: sqlite3.Connection,
    all_raw: list[dict],
    total_cost: float | int,
    errors: list[str],
    on_progress,
) -> dict:
    if not all_raw and errors:
        raise RuntimeError(f"All API calls failed. First error: {errors[0]}")
    if on_progress:
        on_progress("Deduplicating and scoring results…")
    deduped = deduplicate_results(all_raw)

    for item in deduped:
        intent, content_type = classify_intent(item.get("intents"))
        item["intent"] = intent
        item["intent_raw"] = item.pop("intents", None) or {}
        item["content_type"] = content_type
        item["opportunity_raw"] = compute_opportunity(
            volume=item.get("volume") or 0,
            traffic_potential=item.get("traffic_potential"),
            difficulty=item.get("difficulty") or 0,
        )
        item["status"] = "new"
        item["is_local"] = 1 if item["intent_raw"].get("local") or item["intent_raw"].get("is_local") else 0
        serp = item.get("serp_features")
        if isinstance(serp, dict):
            item["serp_features_json"] = json.dumps(serp)
            item["content_format_hint"] = derive_content_format_hint(serp, item["intent"])
        else:
            item["serp_features_json"] = None
            item["content_format_hint"] = ""
        if item.get("source_endpoint") == "site_explorer":
            item.setdefault("competitor_position", item.get("best_position"))
            item.setdefault("competitor_url", item.get("best_position_url"))
            item.setdefault("competitor_position_kind", item.get("best_position_kind"))

    normalize_opportunity_scores(deduped)

    for item in deduped:
        item.pop("opportunity_raw", None)

    existing_raw = get_service_setting(conn, TARGET_KEY, "{}")
    try:
        existing_data = json.loads(existing_raw)
    except json.JSONDecodeError:
        existing_data = {}
    existing_items = existing_data.get("items", [])

    merged = merge_with_existing(existing_items, deduped)
    merged.sort(key=lambda x: x.get("opportunity", 0), reverse=True)

    result = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "unit_cost": total_cost,
        "items": merged,
        "total": len(merged),
        "errors": errors if errors else None,
    }
    set_service_setting(conn, TARGET_KEY, json.dumps(result))
    try:
        sync_keyword_metrics_to_db(conn)
    except Exception:
        logger.exception("Failed to sync keyword metrics to DB (non-fatal)")
    try:
        sync_keyword_page_map(conn)
    except Exception:
        logger.exception("Failed to sync keyword_page_map (non-fatal)")
    try:
        sync_competitor_keyword_gaps(conn)
    except Exception:
        logger.exception("Failed to sync competitor_keyword_gaps (non-fatal)")
    try:
        from shopifyseo.embedding_store import sync_embeddings

        sync_embeddings(conn, object_type="keyword")
        sync_embeddings(conn, object_type="competitor_page")
    except Exception:
        logger.warning("Keyword/competitor embedding sync failed (non-fatal)", exc_info=True)
    return result


def run_seed_keyword_research(conn: sqlite3.Connection, on_progress=None) -> dict:
    """Expand seed keywords via DataForSEO Labs + SERP. Merges into existing target keywords."""
    login, password = _preflight_keyword_research(conn, on_progress)
    cc_iso = _primary_country_iso(conn)

    seed_raw = get_service_setting(conn, "seed_keywords", "[]")
    try:
        seeds = json.loads(seed_raw)
    except json.JSONDecodeError:
        seeds = []
    if not seeds:
        raise RuntimeError("No seed keywords found. Add seed keywords on the Keywords page.")

    seed_strings = [s["keyword"] for s in seeds]
    batches = batch_seeds(seed_strings) if seed_strings else []
    if not batches:
        raise RuntimeError("No valid seed keywords to research.")

    all_raw: list[dict] = []
    total_cost: float = 0.0
    errors: list[str] = []

    for i, batch in enumerate(batches):
        if on_progress:
            on_progress(f"Keyword suggestions ({i + 1}/{len(batches)})")
        try:
            items, cost = call_keyword_suggestions(
                login, password, batch, max_difficulty=70, country_iso=cc_iso
            )
            for item in items:
                item["seed_keywords"] = set(batch)
                item.setdefault("source_endpoint", "keywords_explorer")
            all_raw.extend(items)
            total_cost += cost
        except RuntimeError as exc:
            logger.warning("Skipping keyword-suggestions batch: %s", exc)
            errors.append(str(exc))

    for i, batch in enumerate(batches):
        if on_progress:
            on_progress(f"Autocomplete ({i + 1}/{len(batches)})")
        try:
            items, cost = call_google_autocomplete_suggestions(
                login, password, batch, max_difficulty=70, country_iso=cc_iso
            )
            for item in items:
                item["seed_keywords"] = set(batch)
                item.setdefault("source_endpoint", "keywords_explorer")
            all_raw.extend(items)
            total_cost += cost
        except RuntimeError as exc:
            logger.warning("Skipping autocomplete batch: %s", exc)
            errors.append(str(exc))

    if on_progress:
        on_progress("Keyword ideas (batched seeds)")
    try:
        for j in range(0, len(seed_strings), 200):
            chunk = seed_strings[j : j + 200]
            items, cost = call_keyword_ideas(
                login, password, chunk, max_difficulty=70, country_iso=cc_iso
            )
            seed_set = set(chunk)
            for item in items:
                item["seed_keywords"] = set(seed_set)
                item.setdefault("source_endpoint", "keywords_explorer")
            all_raw.extend(items)
            total_cost += cost
    except RuntimeError as exc:
        logger.warning("Skipping keyword-ideas: %s", exc)
        errors.append(str(exc))

    return _finalize_keyword_research(conn, all_raw, total_cost, errors, on_progress)


def run_competitor_research(conn: sqlite3.Connection, on_progress=None) -> dict:
    """Competitor pipeline via DataForSEO: optional discovery (competitors_domain), organic keywords, profiles, top pages.

    New keywords merge into the same target_keywords store and DB as seed research so clusters / gaps stay in sync.
    """
    login, password = _preflight_keyword_research(conn, on_progress)
    cc_iso = _primary_country_iso(conn)
    competitors = _prepare_competitors_list(conn)
    manual_seed_snapshot = frozenset(competitors)
    our_domain = (get_service_setting(conn, "shopify_domain", "") or "").strip()
    if not competitors and not our_domain:
        raise RuntimeError(
            "Add at least one competitor domain on the Competitors tab, or set your shop domain in Settings "
            "so we can discover similar sites (DataForSEO)."
        )

    all_raw: list[dict] = []
    total_cost: float = 0.0
    errors: list[str] = []
    discovered_profiles: list[dict] = []
    organic_ok = 0
    organic_fail = 0

    if our_domain:
        if on_progress:
            on_progress("Discovering similar sites (DataForSEO organic competitors)…")
        try:
            profiles_raw, cost = call_competitors_domain(
                login, password, our_domain, country_iso=cc_iso, limit=30
            )
            total_cost += float(cost or 0)
            discovered_profiles = [
                p for p in profiles_raw
                if competitor_domain_allowed_for_research(conn, str(p.get("competitor_domain", "")))
            ]
            discovered_domains = [
                norm_competitor_domain(str(p.get("competitor_domain", "")))
                for p in discovered_profiles
                if p.get("competitor_domain")
            ]
            merged_list = list(competitors)
            for d in discovered_domains:
                if d and d not in merged_list:
                    merged_list.append(d)
            if len(merged_list) > len(competitors):
                set_service_setting(conn, "competitor_domains", json.dumps(merged_list))
                logger.info("Auto-merged %d new competitor domains", len(merged_list) - len(competitors))
        except RuntimeError as exc:
            logger.warning("Skipping organic-competitors discovery: %s", exc)
            errors.append(str(exc))

    competitors = _prepare_competitors_list(conn)

    discovered_domains_set = {
        norm_competitor_domain(str(p.get("competitor_domain", "")))
        for p in discovered_profiles
        if p.get("competitor_domain")
    }

    try:
        sync_competitor_profiles(conn, discovered_profiles, list(manual_seed_snapshot))
        now_stub = int(time.time())
        for d in competitors:
            is_man = 1 if d in manual_seed_snapshot else 0
            conn.execute(
                """
                INSERT OR IGNORE INTO competitor_profiles
                    (domain, keywords_common, keywords_they_have, keywords_we_have, share, traffic, is_manual, updated_at)
                VALUES (?, 0, 0, 0, 0, 0, ?, ?)
                """,
                (d, is_man, now_stub),
            )
        conn.commit()
    except Exception:
        logger.exception("Failed to sync competitor_profiles (non-fatal)")

    domains_for_bulk = sorted(
        {norm_competitor_domain(d) for d in competitors if d} | discovered_domains_set
    )
    if domains_for_bulk:
        if on_progress:
            on_progress("DataForSEO — domain organic traffic (bulk estimation)…")
        try:
            traffic_map, bulk_cost = call_bulk_traffic_estimation(
                login, password, domains_for_bulk, country_iso=cc_iso
            )
            total_cost += float(bulk_cost or 0)
            apply_competitor_traffic_from_provider_batch(conn, traffic_map)
        except RuntimeError as exc:
            logger.warning("Bulk traffic estimation failed: %s", exc)
            errors.append(f"Bulk traffic estimation: {exc}")

    for i, domain in enumerate(competitors):
        if on_progress:
            on_progress(
                f"Competitor sites — organic keywords ({i + 1}/{len(competitors)}): {domain}"
            )
        try:
            items, cost = call_ranked_keywords(
                login, password, domain, max_difficulty=70, country_iso=cc_iso
            )
            for item in items:
                item["seed_keywords"] = {domain}
            all_raw.extend(items)
            total_cost += float(cost or 0)
            organic_ok += 1
            if domain not in discovered_domains_set:
                update_competitor_profile_organic_sample_count(conn, domain, items)
        except RuntimeError as exc:
            organic_fail += 1
            logger.warning("Skipping competitor %s: %s", domain, exc)
            errors.append(f"{domain}: {exc}")

    all_comp_domains = sorted(
        {
            norm_competitor_domain(str(p.get("competitor_domain", "")))
            for p in discovered_profiles
            if p.get("competitor_domain")
        }
        | set(competitors)
    )
    for i, domain in enumerate(all_comp_domains):
        if on_progress:
            on_progress(
                f"Competitor sites — top pages by traffic ({i + 1}/{len(all_comp_domains)}): {domain}"
            )
        try:
            pages, cost = call_relevant_pages(login, password, domain, country_iso=cc_iso)
            total_cost += float(cost or 0)
            sync_competitor_top_pages(conn, domain, pages)
        except RuntimeError as exc:
            logger.warning("Skipping top-pages for %s: %s", domain, exc)
            errors.append(f"{domain} (top pages): {exc}")

    meta = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "unit_cost": total_cost,
        "keyword_provider": "dataforseo",
        "errors": errors if errors else [],
        "organic_keywords_ok": organic_ok,
        "organic_keywords_failed": organic_fail,
        "competitors_total": len(competitors),
    }
    try:
        set_service_setting(conn, COMPETITOR_RESEARCH_META_KEY, json.dumps(meta))
    except Exception:
        logger.exception("Failed to persist competitor_research_meta")

    return _finalize_keyword_research(conn, all_raw, total_cost, errors, on_progress)


def refresh_target_keyword_metrics(conn: sqlite3.Connection, on_progress=None) -> dict:
    """Refresh volume/difficulty/CPC for approved target keywords via DataForSEO keyword_overview.

    Only keywords with status ``"approved"`` are sent to the API.  Metrics are
    updated in place while preserving status, GSC data, and competitor fields.
    Returns the updated target keywords dict.
    """
    login, password = _preflight_keyword_research(conn, on_progress)
    cc_iso = _primary_country_iso(conn)

    data = load_target_keywords(conn)
    items = data.get("items", [])
    if not items:
        raise RuntimeError("No target keywords to refresh. Run keyword research first.")

    keyword_strings = [
        item["keyword"]
        for item in items
        if item.get("keyword") and item.get("status") == "approved"
    ]
    if not keyword_strings:
        raise RuntimeError("No approved keywords to refresh. Approve some keywords first.")

    if on_progress:
        on_progress(f"Fetching fresh metrics for {len(keyword_strings)} approved keywords…")

    all_fresh: list[dict] = []
    total_cost = 0.0
    errors: list[str] = []

    # keyword_overview accepts up to 700 per call
    for i in range(0, len(keyword_strings), 700):
        chunk = keyword_strings[i : i + 700]
        batch_num = i // 700 + 1
        total_batches = (len(keyword_strings) + 699) // 700
        if on_progress:
            on_progress(f"Keyword overview ({batch_num}/{total_batches}): {len(chunk)} keywords")
        try:
            rows, cost = call_keyword_overview(login, password, chunk, country_iso=cc_iso)
            all_fresh.extend(rows)
            total_cost += cost
        except RuntimeError as exc:
            logger.warning("Keyword overview batch %d failed: %s", batch_num, exc)
            errors.append(str(exc))

    if not all_fresh and errors:
        raise RuntimeError(f"All metric refresh calls failed. First error: {errors[0]}")

    # Build lookup of fresh metrics by lowercase keyword
    fresh_by_kw: dict[str, dict] = {}
    for row in all_fresh:
        key = row["keyword"].lower()
        # Keep higher-volume entry if duplicates exist
        if key not in fresh_by_kw or row.get("volume", 0) > fresh_by_kw[key].get("volume", 0):
            fresh_by_kw[key] = row

    if on_progress:
        on_progress(f"Updating metrics ({len(fresh_by_kw)} matched of {len(keyword_strings)})…")

    updated_count = 0
    for item in items:
        key = item["keyword"].lower()
        fresh = fresh_by_kw.get(key)
        if not fresh:
            continue
        # Overwrite metrics from fresh data
        item["volume"] = fresh.get("volume", item.get("volume", 0))
        item["difficulty"] = fresh.get("difficulty", item.get("difficulty", 0))
        item["traffic_potential"] = fresh.get("traffic_potential", item.get("traffic_potential", 0))
        item["cpc"] = fresh.get("cpc", item.get("cpc"))
        # Update intent classification
        intent, content_type = classify_intent(fresh.get("intents"))
        item["intent"] = intent
        item["intent_raw"] = fresh.get("intents") or item.get("intent_raw", {})
        item["content_type"] = content_type
        item["is_local"] = 1 if item["intent_raw"].get("local") or item["intent_raw"].get("is_local") else 0
        # Update SERP data
        serp = fresh.get("serp_features")
        if isinstance(serp, dict):
            item["serp_features_json"] = json.dumps(serp)
            item["content_format_hint"] = derive_content_format_hint(serp, item["intent"])
        item["serp_last_update"] = fresh.get("serp_last_update", item.get("serp_last_update"))
        item["parent_topic"] = fresh.get("parent_topic", item.get("parent_topic"))
        # Recompute raw opportunity for this item
        item["opportunity_raw"] = compute_opportunity(
            volume=item.get("volume") or 0,
            traffic_potential=item.get("traffic_potential"),
            difficulty=item.get("difficulty") or 0,
        )
        updated_count += 1
        # Preserve: status, seed_keywords, source_endpoint, gsc_*, ranking_status, competitor_*

    # Re-normalize opportunity scores across all items
    normalize_opportunity_scores(items)
    for item in items:
        item.pop("opportunity_raw", None)

    items.sort(key=lambda x: x.get("opportunity", 0), reverse=True)

    data["items"] = items
    data["total"] = len(items)
    data["metrics_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    data["metrics_refresh_cost"] = total_cost
    if errors:
        data["metrics_refresh_errors"] = errors

    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    try:
        sync_keyword_metrics_to_db(conn)
    except Exception:
        logger.exception("Failed to sync keyword metrics to DB after refresh (non-fatal)")

    if on_progress:
        on_progress(f"Done — refreshed {updated_count} of {len(items)} keywords (${total_cost:.4f})")

    return data


def run_research(conn: sqlite3.Connection, on_progress=None) -> dict:
    """Backward-compatible alias for :func:`run_seed_keyword_research` (seed Keywords Explorer only)."""
    return run_seed_keyword_research(conn, on_progress=on_progress)
