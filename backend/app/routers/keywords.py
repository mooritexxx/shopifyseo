import json
import logging
import queue
import sqlite3
import threading

from fastapi import APIRouter, Body, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from backend.app.db import open_db_connection
from backend.app.services.keyword_research import (
    add_competitor_to_blocklist,
    bulk_update_status,
    cross_reference_gsc,
    load_competitor_blocklist,
    load_target_keywords,
    norm_competitor_domain,
    refresh_target_keyword_metrics,
    remove_competitor_from_blocklist,
    run_competitor_research,
    run_seed_keyword_research,
    update_keyword_status,
    validate_dataforseo_access,
)
from shopifyseo.dashboard_google import get_service_setting, set_service_setting
from shopifyseo.market_context import get_primary_country_code

logger = logging.getLogger(__name__)

VALID_KEYWORD_STATUSES = frozenset({"new", "approved", "dismissed"})

router = APIRouter(prefix="/api/keywords", tags=["keywords"])

SEED_KEY = "seed_keywords"
COMPETITOR_KEY = "competitor_domains"


class SeedKeyword(BaseModel):
    keyword: str
    source: str = "manual"


class SeedKeywordsPayload(BaseModel):
    items: list[SeedKeyword]
    total: int


class SeedKeywordsSaveRequest(BaseModel):
    items: list[SeedKeyword]


class KeywordStatusRequest(BaseModel):
    status: str


class BulkStatusRequest(BaseModel):
    keywords: list[str]
    status: str


def _load_seeds(conn: sqlite3.Connection) -> list[dict]:
    raw = get_service_setting(conn, SEED_KEY, "[]")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _save_seeds(conn: sqlite3.Connection, seeds: list[dict]) -> None:
    set_service_setting(conn, SEED_KEY, json.dumps(seeds))


@router.get("/seed", response_model=dict)
def get_seed_keywords():
    conn = open_db_connection()
    try:
        items = _load_seeds(conn)
        return {"ok": True, "data": {"items": items, "total": len(items)}}
    finally:
        conn.close()


@router.post("/seed", response_model=dict)
def save_seed_keywords(payload: SeedKeywordsSaveRequest):
    conn = open_db_connection()
    try:
        seeds = [item.model_dump() for item in payload.items]
        _save_seeds(conn, seeds)
        return {"ok": True, "data": {"items": seeds, "total": len(seeds)}}
    finally:
        conn.close()


@router.post("/seed/generate", response_model=dict)
def generate_seed_keywords():
    """Auto-generate seed keywords from the Shopify catalog (brands, collections, product attributes)."""
    conn = open_db_connection()
    try:
        seeds: list[dict] = []
        seen: set[str] = set()

        def add(keyword: str, source: str) -> None:
            key = keyword.strip().lower()
            if key and key not in seen:
                seen.add(key)
                seeds.append({"keyword": keyword.strip(), "source": source})

        # Brands / vendors
        rows = conn.execute(
            "SELECT DISTINCT vendor FROM products WHERE vendor != '' ORDER BY vendor"
        ).fetchall()
        for row in rows:
            vendor = row[0] if isinstance(row, (tuple, list)) else row["vendor"]
            add(vendor, "brand")
            add(f"{vendor} vape", "brand")
            add(f"{vendor} disposable vape", "brand")

        # Collections (skip generic ones)
        skip_handles = {"frontpage", "deals", "new-arrivals", "accessories", "coils"}
        rows = conn.execute(
            "SELECT title, handle FROM collections ORDER BY title"
        ).fetchall()
        for row in rows:
            title = row[0] if isinstance(row, (tuple, list)) else row["title"]
            handle = row[1] if isinstance(row, (tuple, list)) else row["handle"]
            if handle in skip_handles:
                continue
            add(title, "collection")

        # Product categories / types
        rows = conn.execute(
            "SELECT DISTINCT product_type FROM products WHERE product_type != ''"
        ).fetchall()
        for row in rows:
            ptype = row[0] if isinstance(row, (tuple, list)) else row["product_type"]
            # Extract the last segment of the taxonomy path
            last = ptype.rsplit(">", 1)[-1].strip()
            if last:
                add(last, "product_type")

        # Common industry terms — country-aware
        from shopifyseo.market_context import get_primary_country_code, country_display_name
        _mkt_name = country_display_name(get_primary_country_code(conn))
        industry_seeds = [
            f"disposable vape {_mkt_name}",
            f"vape {_mkt_name}",
            f"buy vapes online {_mkt_name}",
            "best disposable vape",
            "vape shop online",
            "nicotine salt vape",
            "rechargeable disposable vape",
            "high puff disposable vape",
            f"vape juice {_mkt_name}",
            f"e-liquid {_mkt_name}",
        ]
        for kw in industry_seeds:
            add(kw, "industry")

        # Merge with existing seeds (keep manual ones, add new generated ones)
        existing = _load_seeds(conn)
        for item in existing:
            key = item["keyword"].strip().lower()
            if key not in seen:
                seen.add(key)
                seeds.append(item)

        _save_seeds(conn, seeds)
        return {"ok": True, "data": {"items": seeds, "total": len(seeds)}}
    finally:
        conn.close()


@router.delete("/seed/{keyword}", response_model=dict)
def delete_seed_keyword(keyword: str):
    conn = open_db_connection()
    try:
        items = _load_seeds(conn)
        filtered = [item for item in items if item["keyword"].lower() != keyword.lower()]
        if len(filtered) == len(items):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Keyword not found")
        _save_seeds(conn, filtered)
        return {"ok": True, "data": {"items": filtered, "total": len(filtered)}}
    finally:
        conn.close()


class CompetitorAddRequest(BaseModel):
    domain: str


def _load_competitors(conn: sqlite3.Connection) -> list[str]:
    raw = get_service_setting(conn, COMPETITOR_KEY, "[]")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _save_competitors(conn: sqlite3.Connection, domains: list[str]) -> None:
    set_service_setting(conn, COMPETITOR_KEY, json.dumps(domains))


def _as_int(v: object, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: object, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _competitors_response_data(conn: sqlite3.Connection) -> dict:
    """Build the same payload as GET /competitors (profiles + manual stubs, hide blocklist)."""
    blocklist = load_competitor_blocklist(conn)
    manual_list = _load_competitors(conn)
    rows = conn.execute(
        "SELECT domain, keywords_common, keywords_they_have, keywords_we_have, "
        "share, traffic, is_manual, updated_at FROM competitor_profiles ORDER BY traffic DESC"
    ).fetchall()
    cols = ["domain", "keywords_common", "keywords_they_have", "keywords_we_have",
            "share", "traffic", "is_manual", "updated_at"]
    profiles = {r[0]: dict(zip(cols, r)) for r in rows}
    enriched: list[dict] = []
    seen: set[str] = set()
    for p in rows:
        domain = p[0]
        if domain in blocklist:
            continue
        seen.add(domain)
        enriched.append(profiles[domain])
    for d in manual_list:
        nd = norm_competitor_domain(d)
        if nd in blocklist:
            continue
        if nd not in seen:
            seen.add(nd)
            enriched.append({"domain": nd, "keywords_common": 0, "keywords_they_have": 0,
                             "keywords_we_have": 0, "share": 0, "traffic": 0,
                             "is_manual": 1, "updated_at": 0})
    out: dict = {"items": enriched, "total": len(enriched)}
    raw_meta = get_service_setting(conn, "competitor_research_meta", "")
    if raw_meta:
        try:
            out["last_research"] = json.loads(raw_meta)
        except json.JSONDecodeError:
            pass
    return out


@router.get("/competitors", response_model=dict)
def get_competitors():
    conn = open_db_connection()
    try:
        return {"ok": True, "data": _competitors_response_data(conn)}
    finally:
        conn.close()


@router.get("/competitors/{domain:path}/detail", response_model=dict)
def get_competitor_detail(domain: str):
    conn = open_db_connection()
    try:
        domain = norm_competitor_domain(domain)
        row = conn.execute(
            "SELECT domain, keywords_common, keywords_they_have, keywords_we_have, "
            "share, traffic, is_manual, updated_at FROM competitor_profiles WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row:
            profile = {
                "domain": row[0] or domain,
                "keywords_common": _as_int(row[1]),
                "keywords_they_have": _as_int(row[2]),
                "keywords_we_have": _as_int(row[3]),
                "share": _as_float(row[4]),
                "traffic": _as_int(row[5]),
                "is_manual": _as_int(row[6]),
                "updated_at": _as_int(row[7]),
            }
        else:
            profile = {
                "domain": domain,
                "keywords_common": 0,
                "keywords_they_have": 0,
                "keywords_we_have": 0,
                "share": 0.0,
                "traffic": 0,
                "is_manual": 0,
                "updated_at": 0,
            }
        top_pages = conn.execute(
            "SELECT url, top_keyword, top_keyword_volume, top_keyword_position, "
            "total_keywords, estimated_traffic, traffic_value, page_type "
            "FROM competitor_top_pages WHERE competitor_domain = ? ORDER BY estimated_traffic DESC",
            (domain,),
        ).fetchall()
        top_pages_list = [
            {
                "url": r[0] or "",
                "top_keyword": r[1] if r[1] is not None else "",
                "top_keyword_volume": _as_int(r[2]),
                "top_keyword_position": _as_int(r[3]),
                "total_keywords": _as_int(r[4]),
                "estimated_traffic": _as_int(r[5]),
                "traffic_value": _as_int(r[6]),
                "page_type": r[7] if r[7] is not None else "",
            }
            for r in top_pages
        ]
        gaps = conn.execute(
            "SELECT keyword, competitor_position, competitor_url, our_ranking_status, "
            "our_gsc_position, volume, difficulty, traffic_potential, gap_type "
            "FROM competitor_keyword_gaps WHERE competitor_domain = ? ORDER BY volume DESC LIMIT 200",
            (domain,),
        ).fetchall()
        gaps_list = []
        for r in gaps:
            pos = r[1]
            gsc = r[4]
            gaps_list.append(
                {
                    "keyword": r[0] or "",
                    "competitor_position": None if pos is None else int(pos),
                    "competitor_url": r[2],
                    "our_ranking_status": r[3] if r[3] is not None else "not_ranking",
                    "our_gsc_position": None if gsc is None else float(gsc),
                    "volume": _as_int(r[5]),
                    "difficulty": _as_int(r[6]),
                    "traffic_potential": _as_int(r[7]),
                    "gap_type": r[8] if r[8] is not None else "they_rank_we_dont",
                }
            )
        return {"ok": True, "data": {
            "profile": profile,
            "top_pages": top_pages_list,
            "keyword_gaps": gaps_list,
        }}
    finally:
        conn.close()


@router.post("/competitors", response_model=dict)
def add_competitor(payload: CompetitorAddRequest):
    domain = norm_competitor_domain(payload.domain)
    if not domain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain cannot be empty")
    conn = open_db_connection()
    try:
        remove_competitor_from_blocklist(conn, domain)
        items = _load_competitors(conn)
        norms = {norm_competitor_domain(d) for d in items}
        if domain not in norms:
            items.append(domain)
            _save_competitors(conn, items)
        return {"ok": True, "data": _competitors_response_data(conn)}
    finally:
        conn.close()


@router.post("/competitors/research")
def research_competitors():
    """Stream Site Explorer competitor research via SSE. Declared before DELETE /competitors/{domain:path}
    so POST …/competitors/research is not captured as domain ``research`` (which would yield 405)."""
    q: queue.Queue[str | None] = queue.Queue()

    def on_progress(msg: str) -> None:
        q.put(msg)

    result_holder: dict = {}
    error_holder: list[str] = []

    def worker() -> None:
        conn = open_db_connection()
        try:
            data = run_competitor_research(conn, on_progress=on_progress)
            result_holder["data"] = data
        except RuntimeError as exc:
            error_holder.append(str(exc))
        except Exception as exc:
            logger.exception("Unexpected error during competitor research")
            error_holder.append(f"Unexpected error: {exc}")
        finally:
            conn.close()
            q.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream():
        while True:
            msg = q.get()
            if msg is None:
                break
            yield f"event: progress\ndata: {json.dumps({'message': msg})}\n\n"
        if error_holder:
            yield f"event: error\ndata: {json.dumps({'detail': error_holder[0]})}\n\n"
        elif "data" in result_holder:
            yield f"event: done\ndata: {json.dumps({'ok': True, 'data': result_holder['data']})}\n\n"
        else:
            yield f"event: error\ndata: {json.dumps({'detail': 'Research did not complete — check server logs for details.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/competitors/{domain:path}", response_model=dict)
def delete_competitor(domain: str):
    norm = norm_competitor_domain(domain)
    if not norm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain cannot be empty")
    conn = open_db_connection()
    try:
        items = _load_competitors(conn)
        in_saved = any(norm_competitor_domain(d) == norm for d in items)
        in_profile = conn.execute(
            "SELECT 1 FROM competitor_profiles WHERE domain = ? LIMIT 1", (norm,)
        ).fetchone() is not None
        in_pages = conn.execute(
            "SELECT 1 FROM competitor_top_pages WHERE competitor_domain = ? LIMIT 1", (norm,)
        ).fetchone() is not None
        if not in_saved and not in_profile and not in_pages:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
        filtered = [d for d in items if norm_competitor_domain(d) != norm]
        _save_competitors(conn, filtered)
        conn.execute("DELETE FROM competitor_profiles WHERE domain = ?", (norm,))
        conn.execute("DELETE FROM competitor_top_pages WHERE competitor_domain = ?", (norm,))
        conn.execute("DELETE FROM competitor_keyword_gaps WHERE competitor_domain = ?", (norm,))
        conn.commit()
        add_competitor_to_blocklist(conn, norm)
        return {"ok": True, "data": _competitors_response_data(conn)}
    finally:
        conn.close()


@router.get("/target", response_model=dict)
def get_target_keywords():
    conn = open_db_connection()
    try:
        data = load_target_keywords(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.post("/target/research")
def research_target_keywords():
    """Stream seed Keywords Explorer research via SSE (no Site Explorer competitor calls)."""
    q: queue.Queue[str | None] = queue.Queue()

    def on_progress(msg: str) -> None:
        q.put(msg)

    result_holder: dict = {}
    error_holder: list[str] = []

    def worker() -> None:
        conn = open_db_connection()
        try:
            data = run_seed_keyword_research(conn, on_progress=on_progress)
            result_holder["data"] = data
        except RuntimeError as exc:
            error_holder.append(str(exc))
        except Exception as exc:
            logger.exception("Unexpected error during keyword research")
            error_holder.append(f"Unexpected error: {exc}")
        finally:
            conn.close()
            q.put(None)  # sentinel

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream():
        while True:
            msg = q.get()
            if msg is None:
                break
            yield f"event: progress\ndata: {json.dumps({'message': msg})}\n\n"
        if error_holder:
            yield f"event: error\ndata: {json.dumps({'detail': error_holder[0]})}\n\n"
        elif "data" in result_holder:
            yield f"event: done\ndata: {json.dumps({'ok': True, 'data': result_holder['data']})}\n\n"
        else:
            yield f"event: error\ndata: {json.dumps({'detail': 'Research did not complete — check server logs for details.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class DataforseoValidatePayload(BaseModel):
    """Optional credentials from the Settings form (before save). When empty, values come from the DB."""

    model_config = ConfigDict(extra="ignore")

    dataforseo_api_login: str = ""
    dataforseo_api_password: str = ""


@router.post("/target/validate-dataforseo", response_model=dict)
def validate_dataforseo_credentials(payload: DataforseoValidatePayload | None = Body(default=None)):
    """Quick DataForSEO Labs pre-flight using primary market locale."""
    conn = open_db_connection()
    try:
        login = (payload.dataforseo_api_login if payload else "") or ""
        password = (payload.dataforseo_api_password if payload else "") or ""
        login = login.strip()
        password = password.strip()
        if not login:
            login = (get_service_setting(conn, "dataforseo_api_login") or "").strip()
        if not password:
            password = (get_service_setting(conn, "dataforseo_api_password") or "").strip()
        if not login or not password:
            return {
                "ok": False,
                "detail": "DataForSEO API login and password are required. Add them in Settings > Integrations.",
            }
        iso = (get_primary_country_code(conn) or "CA").strip().upper()
        if len(iso) != 2:
            iso = "CA"
        error = validate_dataforseo_access(login, password, country_iso=iso)
        if error:
            return {"ok": False, "detail": error}
        return {"ok": True, "detail": "DataForSEO API access confirmed."}
    finally:
        conn.close()


@router.post("/target/gsc-crossref", response_model=dict)
def gsc_crossref():
    conn = open_db_connection()
    try:
        data = cross_reference_gsc(conn)
        return {"ok": True, "data": data}
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        conn.close()


@router.post("/target/refresh-metrics")
def refresh_metrics():
    """Refresh volume/difficulty/CPC for all target keywords via DataForSEO keyword_overview."""
    q: queue.Queue[str | None] = queue.Queue()

    def on_progress(msg: str) -> None:
        q.put(msg)

    result_holder: dict = {}
    error_holder: list[str] = []

    def worker() -> None:
        conn = open_db_connection()
        try:
            data = refresh_target_keyword_metrics(conn, on_progress=on_progress)
            result_holder["data"] = data
        except RuntimeError as exc:
            error_holder.append(str(exc))
        except Exception as exc:
            logger.exception("Unexpected error during metrics refresh")
            error_holder.append(f"Unexpected error: {exc}")
        finally:
            conn.close()
            q.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream():
        while True:
            msg = q.get()
            if msg is None:
                break
            yield f"event: progress\ndata: {json.dumps({'message': msg})}\n\n"
        if error_holder:
            yield f"event: error\ndata: {json.dumps({'detail': error_holder[0]})}\n\n"
        elif "data" in result_holder:
            yield f"event: done\ndata: {json.dumps({'ok': True, 'data': result_holder['data']})}\n\n"
        else:
            yield f"event: error\ndata: {json.dumps({'detail': 'Metrics refresh did not complete — check server logs.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.patch("/target/bulk-status", response_model=dict)
def patch_bulk_status(payload: BulkStatusRequest):
    if payload.status not in VALID_KEYWORD_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
    conn = open_db_connection()
    try:
        updated = bulk_update_status(conn, payload.keywords, payload.status)
        return {"ok": True, "data": {"updated": updated}}
    finally:
        conn.close()


@router.patch("/target/{keyword}/status", response_model=dict)
def patch_keyword_status(keyword: str, payload: KeywordStatusRequest):
    if payload.status not in VALID_KEYWORD_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
    conn = open_db_connection()
    try:
        result = update_keyword_status(conn, keyword, payload.status)
        return {"ok": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    finally:
        conn.close()
