"""Keyword and competitor DB sync operations, plus keyword CRUD."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

from shopifyseo.dashboard_google import get_service_setting, set_service_setting

from .competitor_blocklist import norm_competitor_domain
from .keyword_utils import classify_ranking_status, match_gsc_queries, recompute_opportunity_scores

logger = logging.getLogger(__name__)

TARGET_KEY = "target_keywords"
OPPORTUNITY_SCORING_VERSION = 2


def load_target_keywords(conn: sqlite3.Connection) -> dict:
    raw = get_service_setting(conn, TARGET_KEY, "{}")
    if not isinstance(raw, str):
        raw = "{}"
    raw = raw.strip()
    if not raw:
        raw = "{}"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Invalid JSON in %s; using empty target list", TARGET_KEY)
        data = {}
    if not isinstance(data, dict):
        return {"last_run": None, "unit_cost": 0, "items": [], "total": 0}
    items = data.get("items")
    if not items:
        return {"last_run": None, "unit_cost": 0, "items": [], "total": 0}
    if not isinstance(items, list):
        logger.warning("%s.items is not a list (got %s)", TARGET_KEY, type(items).__name__)
        return {"last_run": None, "unit_cost": 0, "items": [], "total": 0}
    clean = [x for x in items if isinstance(x, dict)]
    if len(clean) != len(items):
        logger.warning(
            "Dropped %s non-dict rows from %s",
            len(items) - len(clean),
            TARGET_KEY,
        )
    if not clean:
        return {"last_run": None, "unit_cost": 0, "items": [], "total": 0}
    return {**data, "items": clean, "total": len(clean)}


def refresh_opportunity_scores(conn: sqlite3.Connection, *, force: bool = False) -> dict:
    """Backfill stored target keyword opportunity scores to the current scoring model."""
    data = load_target_keywords(conn)
    items = data.get("items", [])
    if not items:
        return data
    if not force and data.get("opportunity_scoring_version") == OPPORTUNITY_SCORING_VERSION:
        return data

    recompute_opportunity_scores(items)
    items.sort(key=lambda x: x.get("opportunity", 0), reverse=True)
    data["items"] = items
    data["total"] = len(items)
    data["opportunity_scoring_version"] = OPPORTUNITY_SCORING_VERSION
    data["opportunity_scored_at"] = datetime.now(timezone.utc).isoformat()
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    try:
        sync_keyword_metrics_to_db(conn)
    except Exception:
        logger.exception("Failed to sync keyword metrics after opportunity score refresh (non-fatal)")
    return data


def refresh_keyword_metric_opportunity_scores(conn: sqlite3.Connection) -> int:
    """Recompute opportunity directly in ``keyword_metrics`` without trusting stale JSON."""
    rows = conn.execute(
        """
        SELECT keyword, volume, difficulty, traffic_potential, intent,
               ranking_status, gsc_position
        FROM keyword_metrics
        """
    ).fetchall()
    items = [dict(row) for row in rows]
    if not items:
        return 0
    recompute_opportunity_scores(items)
    now = int(time.time())
    for item in items:
        conn.execute(
            "UPDATE keyword_metrics SET opportunity = ?, updated_at = ? WHERE keyword = ?",
            (item.get("opportunity", 0.0), now, item.get("keyword", "")),
        )
    conn.commit()
    return len(items)


_APPROVED_JSON_COLUMNS = ("intent_raw", "seed_keywords", "serp_features")


def load_approved_keywords(conn: sqlite3.Connection) -> list[dict]:
    """Return approved keywords from ``keyword_metrics`` as plain dicts.

    Shape matches what the clustering pipeline consumes: JSON-encoded
    columns are parsed back to Python objects, and ``content_type_label``
    is aliased to ``content_type`` to match the JSON-blob vocabulary.
    """
    rows = conn.execute(
        "SELECT * FROM keyword_metrics WHERE status = 'approved'"
    ).fetchall()
    items: list[dict] = []
    for row in rows:
        d = dict(row)
        d["content_type"] = d.pop("content_type_label", None) or ""
        for col in _APPROVED_JSON_COLUMNS:
            val = d.get(col)
            if isinstance(val, str) and val:
                try:
                    d[col] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    pass
        items.append(d)
    return items


def update_keyword_status(conn: sqlite3.Connection, keyword: str, new_status: str) -> dict:
    data = load_target_keywords(conn)
    found = False
    for item in data["items"]:
        if item["keyword"].lower() == keyword.lower():
            item["status"] = new_status
            found = True
            break
    if not found:
        raise ValueError(f"Keyword not found: {keyword}")
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    conn.execute(
        "UPDATE keyword_metrics SET status = ?, updated_at = ? WHERE LOWER(keyword) = LOWER(?)",
        (new_status, int(time.time()), keyword),
    )
    conn.commit()
    return {"keyword": keyword, "status": new_status}


def bulk_update_status(conn: sqlite3.Connection, keywords: list[str], new_status: str) -> int:
    data = load_target_keywords(conn)
    keyword_set = {kw.lower() for kw in keywords}
    updated = 0
    for item in data["items"]:
        if item["keyword"].lower() in keyword_set:
            item["status"] = new_status
            updated += 1
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    now = int(time.time())
    for kw in keywords:
        conn.execute(
            "UPDATE keyword_metrics SET status = ?, updated_at = ? WHERE LOWER(keyword) = LOWER(?)",
            (new_status, now, kw),
        )
    conn.commit()
    return updated


def sync_keyword_metrics_to_db(conn: sqlite3.Connection) -> int:
    """UPSERT all keyword metrics from the JSON blob into the keyword_metrics table.

    Returns the number of rows synced.
    """
    data = load_target_keywords(conn)
    items = data.get("items", [])
    now = int(time.time())
    for item in items:
        intent_raw = item.get("intent_raw") or {}
        seed_keywords = item.get("seed_keywords") or []
        serp_raw = item.get("serp_features")
        serp_json = item.get("serp_features_json")
        if not serp_json and isinstance(serp_raw, dict):
            serp_json = json.dumps(serp_raw)
        conn.execute(
            """
            INSERT INTO keyword_metrics (
                keyword, volume, difficulty, traffic_potential, cpc,
                intent, content_type_label, intent_raw, parent_topic,
                opportunity, seed_keywords, ranking_status,
                gsc_position, gsc_clicks, gsc_impressions, status, updated_at,
                global_volume, parent_volume, clicks, cps,
                serp_features, word_count, first_seen, serp_last_update,
                source_endpoint, competitor_domain, competitor_position,
                competitor_url, competitor_position_kind,
                is_local, content_format_hint,
                ads_avg_monthly_searches, ads_competition, ads_competition_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                volume = excluded.volume,
                difficulty = excluded.difficulty,
                traffic_potential = excluded.traffic_potential,
                cpc = excluded.cpc,
                intent = excluded.intent,
                content_type_label = excluded.content_type_label,
                intent_raw = excluded.intent_raw,
                parent_topic = excluded.parent_topic,
                opportunity = excluded.opportunity,
                seed_keywords = excluded.seed_keywords,
                ranking_status = excluded.ranking_status,
                gsc_position = excluded.gsc_position,
                gsc_clicks = excluded.gsc_clicks,
                gsc_impressions = excluded.gsc_impressions,
                status = excluded.status,
                updated_at = excluded.updated_at,
                global_volume = COALESCE(excluded.global_volume, keyword_metrics.global_volume),
                parent_volume = COALESCE(excluded.parent_volume, keyword_metrics.parent_volume),
                clicks = COALESCE(excluded.clicks, keyword_metrics.clicks),
                cps = COALESCE(excluded.cps, keyword_metrics.cps),
                serp_features = COALESCE(excluded.serp_features, keyword_metrics.serp_features),
                word_count = COALESCE(excluded.word_count, keyword_metrics.word_count),
                first_seen = COALESCE(excluded.first_seen, keyword_metrics.first_seen),
                serp_last_update = COALESCE(excluded.serp_last_update, keyword_metrics.serp_last_update),
                source_endpoint = COALESCE(excluded.source_endpoint, keyword_metrics.source_endpoint),
                competitor_domain = COALESCE(excluded.competitor_domain, keyword_metrics.competitor_domain),
                competitor_position = COALESCE(excluded.competitor_position, keyword_metrics.competitor_position),
                competitor_url = COALESCE(excluded.competitor_url, keyword_metrics.competitor_url),
                competitor_position_kind = COALESCE(excluded.competitor_position_kind, keyword_metrics.competitor_position_kind),
                is_local = COALESCE(excluded.is_local, keyword_metrics.is_local),
                content_format_hint = COALESCE(excluded.content_format_hint, keyword_metrics.content_format_hint),
                ads_avg_monthly_searches = excluded.ads_avg_monthly_searches,
                ads_competition = excluded.ads_competition,
                ads_competition_index = excluded.ads_competition_index
            """,
            (
                item.get("keyword", ""),
                item.get("volume"),
                item.get("difficulty"),
                item.get("traffic_potential"),
                item.get("cpc"),
                item.get("intent"),
                item.get("content_type"),
                json.dumps(intent_raw),
                item.get("parent_topic"),
                item.get("opportunity"),
                json.dumps(seed_keywords if isinstance(seed_keywords, list) else list(seed_keywords)),
                item.get("ranking_status", "not_ranking"),
                item.get("gsc_position"),
                item.get("gsc_clicks"),
                item.get("gsc_impressions"),
                item.get("status", "new"),
                now,
                item.get("global_volume"),
                item.get("parent_volume"),
                item.get("clicks"),
                item.get("cps"),
                serp_json,
                item.get("word_count"),
                item.get("first_seen"),
                item.get("serp_last_update"),
                item.get("source_endpoint"),
                item.get("competitor_domain"),
                item.get("competitor_position"),
                item.get("competitor_url"),
                item.get("competitor_position_kind"),
                item.get("is_local", 0),
                item.get("content_format_hint", ""),
                item.get("ads_avg_monthly_searches"),
                item.get("ads_competition"),
                item.get("ads_competition_index"),
            ),
        )
    conn.commit()
    return len(items)


def sync_keyword_page_map(conn: sqlite3.Connection) -> int:
    """Populate keyword_page_map from gsc_query_rows, preserving per-page data."""
    now = int(time.time())
    rows = conn.execute(
        """
        SELECT query, object_type, object_handle,
               SUM(clicks) as clicks, SUM(impressions) as impressions,
               AVG(position) as position
        FROM gsc_query_rows
        WHERE object_handle IS NOT NULL AND object_handle != ''
        GROUP BY query, object_type, object_handle
        """
    ).fetchall()
    count = 0
    for query, obj_type, obj_handle, clicks, impressions, position in rows:
        conn.execute(
            """
            INSERT INTO keyword_page_map
                (keyword, object_type, object_handle, source,
                 gsc_clicks, gsc_impressions, gsc_position, updated_at)
            VALUES (?, ?, ?, 'gsc', ?, ?, ?, ?)
            ON CONFLICT(keyword, object_type, object_handle) DO UPDATE SET
                gsc_clicks = excluded.gsc_clicks,
                gsc_impressions = excluded.gsc_impressions,
                gsc_position = excluded.gsc_position,
                updated_at = excluded.updated_at
            """,
            (query, obj_type, obj_handle, int(clicks or 0), int(impressions or 0), position, now),
        )
        count += 1
    conn.commit()
    return count


def sync_competitor_keyword_gaps(conn: sqlite3.Connection) -> int:
    """Build competitor gap records for keywords where they rank and we don't (or rank poorly)."""
    now = int(time.time())
    rows = conn.execute(
        """
        SELECT keyword, competitor_domain, competitor_position,
               competitor_url, ranking_status, gsc_position,
               volume, difficulty, traffic_potential
        FROM keyword_metrics
        WHERE competitor_domain IS NOT NULL AND competitor_domain != ''
          AND (ranking_status != 'ranking' OR gsc_position > 20)
        """
    ).fetchall()
    count = 0
    for kw, comp_domain, comp_pos, comp_url, rank_status, gsc_pos, vol, diff, tp in rows:
        gap_type = "they_rank_we_dont" if rank_status == "not_ranking" else "they_rank_better"
        conn.execute(
            """
            INSERT INTO competitor_keyword_gaps
                (keyword, competitor_domain, competitor_position, competitor_url,
                 our_ranking_status, our_gsc_position, volume, difficulty,
                 traffic_potential, gap_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword, competitor_domain) DO UPDATE SET
                competitor_position = excluded.competitor_position,
                competitor_url = excluded.competitor_url,
                our_ranking_status = excluded.our_ranking_status,
                our_gsc_position = excluded.our_gsc_position,
                volume = excluded.volume,
                difficulty = excluded.difficulty,
                traffic_potential = excluded.traffic_potential,
                gap_type = excluded.gap_type,
                updated_at = excluded.updated_at
            """,
            (kw, comp_domain, comp_pos, comp_url, rank_status, gsc_pos, vol, diff, tp, gap_type, now),
        )
        count += 1
    conn.commit()
    return count


def sync_competitor_profiles(conn: sqlite3.Connection, profiles: list[dict], manual_domains: list[str] | None = None) -> int:
    """UPSERT competitor profile rows from competitor discovery (e.g. Labs ``serp_competitors`` / profiles). `manual_domains` = domains user had before this run (is_manual=1)."""
    now = int(time.time())
    manual = {norm_competitor_domain(d) for d in (manual_domains or []) if d}
    count = 0
    for p in profiles:
        domain = (p.get("competitor_domain") or p.get("domain", "")).strip().lower()
        if not domain:
            continue
        conn.execute(
            """
            INSERT INTO competitor_profiles
                (domain, keywords_common, keywords_they_have, keywords_we_have, share, traffic,
                 labs_visibility, labs_avg_position, labs_median_position, labs_seed_etv, labs_bulk_etv, labs_rating,
                 is_manual, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                keywords_common = excluded.keywords_common,
                keywords_they_have = excluded.keywords_they_have,
                keywords_we_have = excluded.keywords_we_have,
                share = excluded.share,
                traffic = excluded.traffic,
                labs_visibility = excluded.labs_visibility,
                labs_avg_position = excluded.labs_avg_position,
                labs_median_position = excluded.labs_median_position,
                labs_seed_etv = excluded.labs_seed_etv,
                labs_bulk_etv = excluded.labs_bulk_etv,
                labs_rating = excluded.labs_rating,
                is_manual = MAX(competitor_profiles.is_manual, excluded.is_manual),
                updated_at = excluded.updated_at
            """,
            (
                domain,
                p.get("keywords_common", 0),
                p.get("keywords_competitor", 0),
                p.get("keywords_target", 0),
                p.get("share", 0.0),
                p.get("traffic", 0),
                float(p.get("labs_visibility") or 0.0),
                int(p.get("labs_avg_position") or 0),
                int(p.get("labs_median_position") or 0),
                int(p.get("labs_seed_etv") or 0),
                int(p.get("labs_bulk_etv") or 0),
                int(p.get("labs_rating") or 0),
                1 if domain in manual else 0,
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def update_competitor_profile_from_organic_keywords(
    conn: sqlite3.Connection, domain: str, items: list[dict]
) -> None:
    """Set traffic + keyword sample size from Site Explorer organic-keywords (manual competitors often missing from organic-competitors)."""
    if not items:
        return
    domain = norm_competitor_domain(domain)
    n_kw = len(items)
    traffic = 0
    for it in items:
        tp = it.get("traffic_potential")
        if tp is not None:
            try:
                traffic += int(tp)
            except (TypeError, ValueError):
                pass
        else:
            try:
                traffic += int(it.get("volume") or 0)
            except (TypeError, ValueError):
                pass
    now = int(time.time())
    cur = conn.execute(
        """
        UPDATE competitor_profiles SET
            keywords_they_have = ?,
            traffic = ?,
            updated_at = ?
        WHERE domain = ?
        """,
        (n_kw, traffic, now, domain),
    )
    if cur.rowcount == 0:
        conn.execute(
            """
            INSERT INTO competitor_profiles
                (domain, keywords_common, keywords_they_have, keywords_we_have, share, traffic,
                 labs_visibility, labs_avg_position, labs_median_position, labs_seed_etv, labs_bulk_etv, labs_rating,
                 is_manual, updated_at)
                VALUES (?, 0, ?, 0, 0, ?, 0, 0, 0, 0, 0, 0, 1, ?)
                """,
                (domain, n_kw, traffic, now),
            )
    conn.commit()


def update_competitor_profile_organic_sample_count(
    conn: sqlite3.Connection, domain: str, items: list[dict]
) -> None:
    """Set ``keywords_they_have`` from organic-keyword API row count only. Does not change ``traffic``."""
    if not items:
        return
    domain = norm_competitor_domain(domain)
    n_kw = len(items)
    now = int(time.time())
    cur = conn.execute(
        """
        UPDATE competitor_profiles SET
            keywords_they_have = ?,
            updated_at = ?
        WHERE domain = ?
        """,
        (n_kw, now, domain),
    )
    if cur.rowcount == 0:
        conn.execute(
            """
            INSERT INTO competitor_profiles
                (domain, keywords_common, keywords_they_have, keywords_we_have, share, traffic,
                 labs_visibility, labs_avg_position, labs_median_position, labs_seed_etv, labs_bulk_etv, labs_rating,
                 is_manual, updated_at)
                VALUES (?, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, ?)
                """,
                (domain, n_kw, now),
            )
    conn.commit()


def apply_competitor_traffic_from_provider_batch(
    conn: sqlite3.Connection, traffic_by_domain: dict[str, int]
) -> None:
    """Set ``traffic`` from provider-supplied domain-level estimates (e.g. DataForSEO bulk organic ETV)."""
    if not traffic_by_domain:
        return
    now = int(time.time())
    for raw_dom, traffic in traffic_by_domain.items():
        domain = norm_competitor_domain(str(raw_dom))
        if not domain:
            continue
        try:
            tval = int(traffic)
        except (TypeError, ValueError):
            tval = 0
        tval = max(0, tval)
        cur = conn.execute(
            """
            UPDATE competitor_profiles SET traffic = ?, updated_at = ?
            WHERE domain = ?
            """,
            (tval, now, domain),
        )
        if cur.rowcount == 0:
            conn.execute(
                """
                INSERT INTO competitor_profiles
                    (domain, keywords_common, keywords_they_have, keywords_we_have, share, traffic,
                     labs_visibility, labs_avg_position, labs_median_position, labs_seed_etv, labs_bulk_etv, labs_rating,
                     is_manual, updated_at)
                VALUES (?, 0, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, ?)
                """,
                (domain, tval, now),
            )
    conn.commit()


def sync_competitor_top_pages(conn: sqlite3.Connection, domain: str, pages: list[dict]) -> int:
    """UPSERT top-page rows for a single competitor domain."""
    now = int(time.time())
    domain = domain.strip().lower()
    count = 0
    for p in pages:
        page_url = p.get("url", "").strip()
        if not page_url:
            continue
        conn.execute(
            """
            INSERT INTO competitor_top_pages
                (competitor_domain, url, top_keyword, top_keyword_volume, top_keyword_position,
                 total_keywords, estimated_traffic, traffic_value, page_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(competitor_domain, url) DO UPDATE SET
                top_keyword = excluded.top_keyword,
                top_keyword_volume = excluded.top_keyword_volume,
                top_keyword_position = excluded.top_keyword_position,
                total_keywords = excluded.total_keywords,
                estimated_traffic = excluded.estimated_traffic,
                traffic_value = excluded.traffic_value,
                page_type = excluded.page_type,
                updated_at = excluded.updated_at
            """,
            (
                domain,
                page_url,
                p.get("top_keyword", ""),
                p.get("top_keyword_volume", 0),
                p.get("top_keyword_best_position", 0),
                p.get("keywords", 0),
                p.get("sum_traffic", 0),
                p.get("value", 0),
                p.get("page_type", ""),
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def sync_competitor_top_pages_from_keyword_metrics(
    conn: sqlite3.Connection,
    *,
    per_domain_limit: int = 50,
) -> int:
    """Backfill competitor top pages from keyword-level competitor URLs.

    DataForSEO's relevant-pages endpoint can return no rows for some runs, while
    keyword_metrics still contains useful competitor URLs from ranked-keyword data.
    This makes those URLs embeddable and available to competitive-gap RAG.
    """
    rows = conn.execute(
        """
        SELECT keyword,
               competitor_domain,
               competitor_url,
               competitor_position,
               volume,
               traffic_potential,
               opportunity,
               content_type_label,
               intent
        FROM keyword_metrics
        WHERE competitor_domain IS NOT NULL
          AND TRIM(competitor_domain) != ''
          AND competitor_url IS NOT NULL
          AND TRIM(competitor_url) != ''
        """
    ).fetchall()
    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        domain = norm_competitor_domain(row["competitor_domain"] or "")
        url = (row["competitor_url"] or "").strip()
        keyword = (row["keyword"] or "").strip()
        if not domain or not url or not keyword:
            continue
        try:
            position = int(float(row["competitor_position"] or 0))
        except (TypeError, ValueError):
            position = 0
        try:
            volume = int(float(row["volume"] or 0))
        except (TypeError, ValueError):
            volume = 0
        try:
            traffic = int(float(row["traffic_potential"] or volume or 0))
        except (TypeError, ValueError):
            traffic = volume
        try:
            opportunity = float(row["opportunity"] or 0)
        except (TypeError, ValueError):
            opportunity = 0.0
        by_url = grouped.setdefault(domain, {})
        page = by_url.setdefault(
            url,
            {
                "url": url,
                "top_keyword": keyword,
                "top_keyword_volume": volume,
                "top_keyword_best_position": position,
                "keywords": 0,
                "sum_traffic": 0,
                "value": 0,
                "page_type": (row["content_type_label"] or row["intent"] or ""),
                "_best_rank_tuple": (position if position > 0 else 9999, -volume, -opportunity),
            },
        )
        page["keywords"] += 1
        page["sum_traffic"] += max(traffic, 0)
        rank_tuple = (position if position > 0 else 9999, -volume, -opportunity)
        if rank_tuple < page["_best_rank_tuple"]:
            page["top_keyword"] = keyword
            page["top_keyword_volume"] = volume
            page["top_keyword_best_position"] = position
            page["page_type"] = (row["content_type_label"] or row["intent"] or page.get("page_type") or "")
            page["_best_rank_tuple"] = rank_tuple

    total = 0
    for domain, by_url in grouped.items():
        pages = sorted(
            by_url.values(),
            key=lambda p: (
                -int(p.get("sum_traffic") or 0),
                -int(p.get("top_keyword_volume") or 0),
                int(p.get("top_keyword_best_position") or 9999),
                p.get("url") or "",
            ),
        )[: max(1, per_domain_limit)]
        clean_pages = []
        for page in pages:
            clean = {k: v for k, v in page.items() if not k.startswith("_")}
            clean_pages.append(clean)
        total += sync_competitor_top_pages(conn, domain, clean_pages)
    return total


def cross_reference_gsc(conn: sqlite3.Connection) -> dict:
    """Enrich target keywords with GSC ranking data."""
    data = load_target_keywords(conn)
    items = data.get("items", [])
    if not items:
        return data

    rows = conn.execute("""
        SELECT
            LOWER(query) as query,
            MIN(position) as best_position,
            SUM(clicks) as total_clicks,
            SUM(impressions) as total_impressions
        FROM gsc_query_rows
        GROUP BY LOWER(query)
    """).fetchall()

    gsc_data: dict[str, dict] = {}
    for row in rows:
        gsc_data[row[0]] = {
            "position": row[1],
            "clicks": row[2],
            "impressions": row[3],
        }

    for item in items:
        match = match_gsc_queries(item["keyword"], gsc_data)
        if match:
            item["gsc_position"] = round(match["position"], 1)
            item["gsc_clicks"] = match["clicks"]
            item["gsc_impressions"] = match["impressions"]
            item["ranking_status"] = classify_ranking_status(match["position"])
        else:
            item["gsc_position"] = None
            item["gsc_clicks"] = None
            item["gsc_impressions"] = None
            item["ranking_status"] = "not_ranking"

    recompute_opportunity_scores(items)
    items.sort(key=lambda x: x.get("opportunity", 0), reverse=True)
    data["opportunity_scoring_version"] = OPPORTUNITY_SCORING_VERSION
    data["opportunity_scored_at"] = datetime.now(timezone.utc).isoformat()
    data["gsc_crossref_at"] = datetime.now(timezone.utc).isoformat()
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    try:
        sync_keyword_metrics_to_db(conn)
    except Exception:
        logger.exception("Failed to sync keyword metrics to DB after GSC crossref (non-fatal)")
    try:
        sync_keyword_page_map(conn)
    except Exception:
        logger.exception("Failed to sync keyword_page_map after GSC crossref (non-fatal)")
    try:
        sync_competitor_keyword_gaps(conn)
    except Exception:
        logger.exception("Failed to sync competitor_keyword_gaps after GSC crossref (non-fatal)")
    try:
        sync_competitor_top_pages_from_keyword_metrics(conn, per_domain_limit=50)
    except Exception:
        logger.exception("Failed to sync competitor_top_pages from keyword metrics after GSC crossref (non-fatal)")
    try:
        from shopifyseo.embedding_store import sync_embeddings
        sync_embeddings(conn, object_type="keyword")
        sync_embeddings(conn, object_type="competitor_page")
    except Exception:
        logger.warning("Keyword/competitor embedding sync failed (non-fatal)", exc_info=True)
    return data
