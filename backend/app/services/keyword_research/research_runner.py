"""Research orchestration — seed and competitor pipelines (DataForSEO Labs)."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

from shopifyseo.dashboard_google import get_service_setting, set_service_setting

from .dataforseo_client import (
    DFS_SERP_COMPETITORS_DISCOVERY_LIMIT,
    DFS_SERP_COMPETITORS_FETCH_LIMIT,
    _norm_domain,
    call_bulk_traffic_estimation,
    call_google_autocomplete_suggestions,
    call_keyword_ideas,
    call_keyword_overview,
    call_keyword_suggestions,
    call_ranked_keywords,
    call_relevant_pages,
    call_serp_competitors,
    validate_dataforseo_access,
)
from .competitor_blocklist import (
    competitor_domain_allowed_for_research,
    load_competitor_blocklist,
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
COMPETITOR_DISCOVERY_PENDING_KEY = "competitor_discovery_pending"


def resolve_competitor_labs_target_domain(conn: sqlite3.Connection) -> str:
    """Normalized hostname for Labs competitor flows: public store domain, then Shopify hostname."""
    for key in ("store_custom_domain", "shopify_shop"):
        raw = (get_service_setting(conn, key, "") or "").strip()
        if not raw:
            continue
        dom = _norm_domain(raw)
        if dom:
            return dom
    return ""


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


def _load_seed_keyword_strings(conn: sqlite3.Connection) -> list[str]:
    """Ordered unique seed keyword strings from ``seed_keywords`` service setting.

    Labs ``serp_competitors`` accepts at most 200 keywords per request; :func:`call_serp_competitors` truncates
    to that cap in order listed here.
    """
    raw = get_service_setting(conn, "seed_keywords", "[]")
    try:
        seeds = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(seeds, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in seeds:
        if not isinstance(s, dict):
            continue
        kw = (s.get("keyword") or "").strip()
        if not kw:
            continue
        low = kw.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(kw)
    return out


def _serp_rows_to_discovery_profiles(
    rows: list[dict],
    *,
    seed_total: int,
    bulk_traffic_by_domain: dict[str, int] | None = None,
) -> list[dict]:
    """Map Labs ``serp_competitors`` items to the profile dict shape used by discovery + ``sync_competitor_profiles``.

    When ``bulk_traffic_by_domain`` is set (full-domain organic ETV from Labs bulk traffic estimation),
    ``traffic`` is ``max(bulk_etv, seed_etv)`` so we never show a number below the seed-keyword slice when bulk
    is missing or anomalously low for a domain.
    """
    seed_total = max(int(seed_total), 1)
    bulk = bulk_traffic_by_domain or {}
    profiles: list[dict] = []
    for r in rows:
        raw_dom = str(r.get("domain") or "")
        dom = norm_competitor_domain(raw_dom)
        if not dom:
            continue
        kc = int(r.get("keywords_count") or 0)
        rating = int(r.get("rating") or 0)
        try:
            seed_etv = int(float(r.get("etv") or 0))
        except (TypeError, ValueError):
            seed_etv = 0
        bulk_etv = int(bulk.get(dom, 0))
        traffic = max(bulk_etv, seed_etv)
        share = min(1.0, kc / float(seed_total))
        try:
            vis = float(r.get("visibility") or 0.0)
        except (TypeError, ValueError):
            vis = 0.0
        profiles.append(
            {
                "competitor_domain": dom,
                "keywords_common": kc,
                "keywords_competitor": rating,
                "keywords_target": seed_total,
                "share": float(share),
                "traffic": int(traffic),
                "labs_visibility": vis,
                "labs_avg_position": int(r.get("avg_position") or 0),
                "labs_median_position": int(r.get("median_position") or 0),
                "labs_seed_etv": seed_etv,
                "labs_bulk_etv": bulk_etv,
                "labs_rating": rating,
            }
        )
    profiles.sort(key=lambda p: int(p.get("traffic") or 0), reverse=True)
    return profiles


def _serp_discovery_profiles_with_bulk_traffic(
    login: str,
    password: str,
    seed_strings: list[str],
    *,
    country_iso: str,
    out_limit: int,
) -> tuple[list[dict], float]:
    """SERP competitors for seeds (wide pool), bulk full-domain organic ETV, sort by traffic.

    Returns **all** ranked profiles from the fetch pool (typically up to :data:`DFS_SERP_COMPETITORS_FETCH_LIMIT` rows).
    ``out_limit`` only raises the Labs fetch floor so small caps still request a wide SERP pool.
    """
    fetch_limit = max(out_limit, min(DFS_SERP_COMPETITORS_FETCH_LIMIT, 1000))
    rows, c1 = call_serp_competitors(
        login, password, seed_strings, country_iso=country_iso, limit=fetch_limit
    )
    cost = float(c1 or 0.0)
    if not rows:
        return [], cost
    doms: list[str] = []
    seen: set[str] = set()
    for r in rows:
        d = norm_competitor_domain(str(r.get("domain") or ""))
        if d and d not in seen:
            seen.add(d)
            doms.append(d)
    bulk_map, c2 = call_bulk_traffic_estimation(login, password, doms, country_iso=country_iso)
    cost += float(c2 or 0.0)
    n_kw = min(len(seed_strings), 200)
    profiles = _serp_rows_to_discovery_profiles(rows, seed_total=n_kw, bulk_traffic_by_domain=bulk_map)
    # Return the full ranked pool (up to ``fetch_limit`` domains from Labs). Callers filter (e.g. saved / dismissed /
    # own store) then cap to ``DFS_SERP_COMPETITORS_DISCOVERY_LIMIT`` so filtered rows do not consume suggestion slots.
    return profiles, cost


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
    """Competitor pipeline via DataForSEO: optional discovery (``serp_competitors`` on seed keywords), organic keywords, profiles, top pages.

    New keywords merge into the same target_keywords store and DB as seed research so clusters / gaps stay in sync.
    """
    login, password = _preflight_keyword_research(conn, on_progress)
    cc_iso = _primary_country_iso(conn)
    competitors = _prepare_competitors_list(conn)
    manual_seed_snapshot = frozenset(competitors)
    our_domain = resolve_competitor_labs_target_domain(conn)
    if not competitors and not our_domain:
        raise RuntimeError(
            "Add at least one competitor domain on the Competitors tab, or set your website URL for discovery above, "
            "or your public store domain / Shopify shop in Settings so we can discover similar sites (DataForSEO)."
        )

    all_raw: list[dict] = []
    total_cost: float = 0.0
    errors: list[str] = []
    discovered_profiles: list[dict] = []
    organic_ok = 0
    organic_fail = 0

    if our_domain:
        seed_strings = _load_seed_keyword_strings(conn)
        if seed_strings:
            if on_progress:
                on_progress("Discovering sites for your seed keywords (SERP + organic traffic)…")
            try:
                profiles_raw, dcost = _serp_discovery_profiles_with_bulk_traffic(
                    login,
                    password,
                    seed_strings,
                    country_iso=cc_iso,
                    out_limit=DFS_SERP_COMPETITORS_DISCOVERY_LIMIT,
                )
                total_cost += dcost
                blocklist = load_competitor_blocklist(conn)
                discovered_profiles = []
                for p in profiles_raw:
                    if len(discovered_profiles) >= DFS_SERP_COMPETITORS_DISCOVERY_LIMIT:
                        break
                    dom = norm_competitor_domain(str(p.get("competitor_domain", "")))
                    if not dom:
                        continue
                    if our_domain and dom == our_domain:
                        continue
                    if dom in manual_seed_snapshot:
                        continue
                    if dom in blocklist:
                        continue
                    discovered_profiles.append(p)
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
                logger.warning("Skipping serp competitor discovery: %s", exc)
                errors.append(str(exc))
        else:
            logger.info("Skipping serp competitor discovery: no seed keywords in settings.")

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
                    (domain, keywords_common, keywords_they_have, keywords_we_have, share, traffic,
                     labs_visibility, labs_avg_position, labs_median_position, labs_seed_etv, labs_bulk_etv, labs_rating,
                     is_manual, updated_at)
                VALUES (?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
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


def load_competitor_discovery_pending(conn: sqlite3.Connection) -> list[dict]:
    raw = get_service_setting(conn, COMPETITOR_DISCOVERY_PENDING_KEY, "")
    if not (raw or "").strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def save_competitor_discovery_pending(conn: sqlite3.Connection, rows: list[dict]) -> None:
    set_service_setting(conn, COMPETITOR_DISCOVERY_PENDING_KEY, json.dumps(rows))


def run_discover_competitors_for_review(
    conn: sqlite3.Connection,
    *,
    on_progress=None,
) -> dict:
    """Labs ``serp_competitors`` on seed keywords; replaces pending suggestions (does not merge into ``competitor_domains``).

    Drops your store (when Settings has a public domain), domains already saved as competitors, and domains on the
    competitor blocklist (persisted when you reject a suggestion or remove a saved competitor).
    """
    login, password = _preflight_keyword_research(conn, on_progress)
    cc_iso = _primary_country_iso(conn)
    seed_strings = _load_seed_keyword_strings(conn)
    if not seed_strings:
        raise RuntimeError("Add seed keywords on the Seed Keywords tab first, then find competitors.")
    target = resolve_competitor_labs_target_domain(conn)
    competitors_set = set(_prepare_competitors_list(conn))
    blocklist = load_competitor_blocklist(conn)
    if on_progress:
        on_progress(f"Fetching SERP competitors for {len(seed_strings)} seeds and organic traffic estimates…")
    profiles_raw, cost = _serp_discovery_profiles_with_bulk_traffic(
        login,
        password,
        seed_strings,
        country_iso=cc_iso,
        out_limit=DFS_SERP_COMPETITORS_DISCOVERY_LIMIT,
    )
    suggestions: list[dict] = []
    for p in profiles_raw:
        if len(suggestions) >= DFS_SERP_COMPETITORS_DISCOVERY_LIMIT:
            break
        dom = norm_competitor_domain(str(p.get("competitor_domain", "")))
        if not dom:
            continue
        if target and dom == target:
            continue
        if dom in competitors_set:
            continue
        if dom in blocklist:
            continue
        suggestions.append(
            {
                "domain": dom,
                "keywords_common": int(p.get("keywords_common") or 0),
                "keywords_they_have": int(p.get("keywords_competitor") or 0),
                "keywords_we_have": int(p.get("keywords_target") or 0),
                "share": float(p.get("share") or 0.0),
                "traffic": int(p.get("traffic") or 0),
                "labs_visibility": float(p.get("labs_visibility") or 0.0),
                "labs_avg_position": int(p.get("labs_avg_position") or 0),
                "labs_median_position": int(p.get("labs_median_position") or 0),
                "labs_seed_etv": int(p.get("labs_seed_etv") or 0),
                "labs_bulk_etv": int(p.get("labs_bulk_etv") or 0),
                "labs_rating": int(p.get("labs_rating") or 0),
                "is_manual": 0,
                "updated_at": 0,
            }
        )
    save_competitor_discovery_pending(conn, suggestions)
    return {
        "suggestions": suggestions,
        "target_domain": target,
        "unit_cost": float(cost or 0.0),
    }


def run_research(conn: sqlite3.Connection, on_progress=None) -> dict:
    """Backward-compatible alias for :func:`run_seed_keyword_research` (seed Keywords Explorer only)."""
    return run_seed_keyword_research(conn, on_progress=on_progress)
