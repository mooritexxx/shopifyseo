"""Open PageRank client — domain authority for competitor profiles.

Open PageRank (domcop.com/openpagerank) publishes a 0–10 domain authority score
derived from the Common Crawl open web graph. The free tier covers 30,000
domains/month at 100 domains per request.

Scope, deliberately: this is a **domain-level** metric and is used only to rank
competitor domains. It is not a keyword-difficulty source and must not be used
to synthesize one — unknown keyword difficulty stays unknown (see
``keyword_research/keyword_utils``).
"""

from __future__ import annotations

import logging
import sqlite3
import time

from shopifyseo.dashboard_google import get_service_setting
from shopifyseo.dashboard_http import HttpRequestError, request_json

logger = logging.getLogger(__name__)

# POST with a JSON body — see https://openpagerank.keywordseverywhere.com/docs
OPR_BULK_URL = "https://openpagerank.keywordseverywhere.com/v1/domains/bulk"
OPR_SETTING_KEY = "open_page_rank_api_key"

# The API accepts up to 100 domains per call.
OPR_BATCH_SIZE = 100
# Free tier allows 60 requests/minute; stay under it on multi-batch runs.
OPR_BATCH_DELAY_SEC = 1.1


def get_open_page_rank_key(conn: sqlite3.Connection) -> str:
    return (get_service_setting(conn, OPR_SETTING_KEY) or "").strip()


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key.strip()}"}


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _opt_int(raw) -> int | None:
    if raw in (None, "", "N/A"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def fetch_domain_authority(api_key: str, domains: list[str]) -> dict[str, dict]:
    """Return ``{domain: {"authority", "rank", "referring_domains"}}`` for *domains*.

    Domains Open PageRank does not know come back with ``found: false`` and are
    omitted from the result rather than recorded as 0 — an unindexed domain is
    unknown, not authority zero.
    """
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("Open PageRank API key is required. Add it in Settings > Integrations.")
    uniq = sorted({(d or "").strip().lower() for d in domains if (d or "").strip()})
    if not uniq:
        return {}

    out: dict[str, dict] = {}
    batches = _chunk(uniq, OPR_BATCH_SIZE)
    for idx, batch in enumerate(batches):
        try:
            resp = request_json(
                OPR_BULK_URL,
                method="POST",
                headers=_auth_headers(key),
                payload={"domains": batch, "include_history": False},
                timeout=60,
            )
        except HttpRequestError as exc:
            _raise_opr_http_error(exc.status, (exc.body or "")[:300])
        if not isinstance(resp, dict):
            raise RuntimeError("Open PageRank returned a non-object response.")
        for row in resp.get("results") or []:
            if not isinstance(row, dict):
                continue
            domain = (row.get("domain") or "").strip().lower()
            if not domain or not row.get("found"):
                continue
            raw = row.get("open_page_rank")
            if raw in (None, "", "N/A"):
                continue
            try:
                authority = float(raw)
            except (TypeError, ValueError):
                continue
            out[domain] = {
                "authority": authority,
                "rank": _opt_int(row.get("rank")),
                "referring_domains": _opt_int(row.get("referring_domains")),
            }
        if idx < len(batches) - 1:
            time.sleep(OPR_BATCH_DELAY_SEC)
    return out


def _raise_opr_http_error(status: int | None, body: str) -> None:
    if status == 401:
        raise RuntimeError(
            "Open PageRank: authentication failed (401). Keys for this API start with "
            "'opr_live_' — a bare 20-character hex string is a legacy domcop key and is no "
            "longer accepted. Copy the current key from your Open PageRank dashboard into "
            "Settings > Integrations."
        )
    if status == 403:
        raise RuntimeError(f"Open PageRank: forbidden (403). {body}")
    if status == 429:
        raise RuntimeError(
            "Open PageRank: rate limit or monthly quota exceeded (429). The free tier allows "
            "30,000 domains/month at 60 requests/minute."
        )
    raise RuntimeError(f"Open PageRank HTTP error ({status or '?'}): {body}") from None


def resolve_site_domain(conn: sqlite3.Connection) -> str:
    """Storefront domain to score: the custom domain, else the myshopify host."""
    for key in ("store_custom_domain", "shopify_shop"):
        raw = (get_service_setting(conn, key) or "").strip().lower()
        if not raw:
            continue
        for prefix in ("https://", "http://"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if raw.startswith("www."):
            raw = raw[4:]
        raw = raw.split("/")[0].strip()
        if raw:
            return raw
    return ""


def fetch_site_authority(api_key: str, domain: str) -> dict:
    """Return the current score plus full monthly history for one domain.

    ``found`` is False when Open PageRank has no entry for the domain — new or
    lightly-linked sites are simply absent from the Common Crawl web graph. That
    is reported as-is; it is not an authority of zero.
    """
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("Open PageRank API key is required. Add it in Settings > Integrations.")
    dom = (domain or "").strip().lower()
    if not dom:
        return {"domain": "", "found": False, "history": []}

    try:
        resp = request_json(
            OPR_BULK_URL,
            method="POST",
            headers=_auth_headers(key),
            payload={"domains": [dom], "include_history": True},
            timeout=60,
        )
    except HttpRequestError as exc:
        _raise_opr_http_error(exc.status, (exc.body or "")[:300])
    if not isinstance(resp, dict):
        raise RuntimeError("Open PageRank returned a non-object response.")

    row = next(
        (r for r in (resp.get("results") or []) if isinstance(r, dict)),
        None,
    )
    if not row or not row.get("found"):
        return {"domain": dom, "found": False, "history": []}

    history = []
    for point in row.get("history") or []:
        if not isinstance(point, dict):
            continue
        date = (point.get("date") or "").strip()
        raw = point.get("open_page_rank")
        if not date or raw in (None, "", "N/A"):
            continue
        try:
            history.append(
                {
                    "date": date,
                    "authority": float(raw),
                    "estimated": bool(point.get("estimated")),
                }
            )
        except (TypeError, ValueError):
            continue
    history.sort(key=lambda p: p["date"])

    try:
        authority = float(row.get("open_page_rank"))
    except (TypeError, ValueError):
        authority = None
    return {
        "domain": dom,
        "found": True,
        "authority": authority,
        "rank": _opt_int(row.get("rank")),
        "referring_domains": _opt_int(row.get("referring_domains")),
        "as_of": (resp.get("as_of") or "").strip() or None,
        "history": history,
    }


def refresh_site_authority(conn: sqlite3.Connection) -> dict:
    """Fetch and persist the storefront's own authority plus monthly history."""
    api_key = get_open_page_rank_key(conn)
    if not api_key:
        raise RuntimeError("Open PageRank API key is required. Add it in Settings > Integrations.")
    domain = resolve_site_domain(conn)
    if not domain:
        raise RuntimeError(
            "No storefront domain configured. Set Store custom domain in Settings > Data Sources."
        )

    result = fetch_site_authority(api_key, domain)
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO site_authority (domain, found, authority_score, authority_rank,
                                    referring_domains, as_of, checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            found = excluded.found,
            authority_score = excluded.authority_score,
            authority_rank = excluded.authority_rank,
            referring_domains = excluded.referring_domains,
            as_of = excluded.as_of,
            checked_at = excluded.checked_at
        """,
        (
            domain,
            1 if result.get("found") else 0,
            result.get("authority"),
            result.get("rank"),
            result.get("referring_domains"),
            result.get("as_of"),
            now,
        ),
    )
    for point in result.get("history") or []:
        conn.execute(
            """
            INSERT INTO site_authority_history (domain, as_of, authority_score, estimated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(domain, as_of) DO UPDATE SET
                authority_score = excluded.authority_score,
                estimated = excluded.estimated
            """,
            (domain, point["date"], point["authority"], 1 if point["estimated"] else 0),
        )
    conn.commit()
    return load_site_authority(conn)


def load_site_authority(conn: sqlite3.Connection) -> dict:
    """Read the stored storefront authority snapshot + history for the dashboard."""
    domain = resolve_site_domain(conn)
    if not domain:
        return {"domain": "", "found": False, "history": [], "checked_at": None}
    row = conn.execute(
        """
        SELECT domain, found, authority_score, authority_rank, referring_domains, as_of, checked_at
          FROM site_authority WHERE domain = ?
        """,
        (domain,),
    ).fetchone()
    history = [
        {"date": r[0], "authority": r[1], "estimated": bool(r[2])}
        for r in conn.execute(
            "SELECT as_of, authority_score, estimated FROM site_authority_history"
            " WHERE domain = ? ORDER BY as_of",
            (domain,),
        )
    ]
    if not row:
        return {"domain": domain, "found": False, "history": history, "checked_at": None}
    return {
        "domain": row[0],
        "found": bool(row[1]),
        "authority": row[2],
        "rank": row[3],
        "referring_domains": row[4],
        "as_of": row[5],
        "checked_at": row[6],
        "history": history,
    }


def competitor_authority_benchmark(conn: sqlite3.Connection) -> dict:
    """Scored-competitor context so the dashboard card is useful before indexing."""
    row = conn.execute(
        """
        SELECT COUNT(*), ROUND(AVG(authority_score), 2), MAX(authority_score)
          FROM competitor_profiles WHERE authority_score IS NOT NULL
        """
    ).fetchone()
    top = conn.execute(
        """
        SELECT domain, authority_score FROM competitor_profiles
         WHERE authority_score IS NOT NULL ORDER BY authority_score DESC LIMIT 1
        """
    ).fetchone()
    return {
        "scored_competitors": row[0] or 0,
        "avg_authority": row[1],
        "max_authority": row[2],
        "top_domain": top[0] if top else None,
    }


def refresh_competitor_authority(conn: sqlite3.Connection) -> dict:
    """Fetch Open PageRank authority for every competitor domain and store it.

    Returns ``{"checked": n, "scored": n, "unknown": n}``. Domains absent from the
    Open PageRank index keep ``authority_score = NULL`` rather than being set to 0.
    """
    api_key = get_open_page_rank_key(conn)
    if not api_key:
        raise RuntimeError("Open PageRank API key is required. Add it in Settings > Integrations.")

    domains = [
        r[0] for r in conn.execute("SELECT domain FROM competitor_profiles ORDER BY domain")
    ]
    if not domains:
        return {"checked": 0, "scored": 0, "unknown": 0}

    scores = fetch_domain_authority(api_key, domains)
    now = int(time.time())
    for domain in domains:
        hit = scores.get(domain.strip().lower())
        conn.execute(
            """
            UPDATE competitor_profiles
               SET authority_score = ?, authority_rank = ?,
                   referring_domains = ?, authority_updated_at = ?
             WHERE domain = ?
            """,
            (
                hit["authority"] if hit else None,
                hit["rank"] if hit else None,
                hit["referring_domains"] if hit else None,
                now,
                domain,
            ),
        )
    conn.commit()
    scored = sum(1 for d in domains if d.strip().lower() in scores)
    return {"checked": len(domains), "scored": scored, "unknown": len(domains) - scored}


def validate_open_page_rank_access(api_key: str) -> str | None:
    """Cheap single-domain probe. Returns None when OK, else an error string."""
    if not (api_key or "").strip():
        return "Open PageRank API key is required. Add it in Settings > Integrations."
    try:
        result = fetch_domain_authority(api_key, ["google.com"])
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - surfaced to the settings UI
        return f"Open PageRank connection error: {exc}"
    if not result:
        return "Open PageRank responded but returned no data for the probe domain."
    return None
