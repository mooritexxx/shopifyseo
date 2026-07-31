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
from urllib.parse import urlencode

from shopifyseo.dashboard_google import get_service_setting
from shopifyseo.dashboard_http import HttpRequestError, request_json

logger = logging.getLogger(__name__)

OPR_BASE = "https://openpagerank.keywordseverywhere.com/api/v1.0"
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


def fetch_domain_authority(api_key: str, domains: list[str]) -> dict[str, dict]:
    """Return ``{domain: {"authority": float, "rank": int|None}}`` for *domains*.

    Domains Open PageRank does not know are omitted from the result rather than
    returned as 0 — an unranked domain is unknown, not authority zero.
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
        query = urlencode([("domains[]", d) for d in batch])
        url = f"{OPR_BASE}/getPageRank?{query}"
        try:
            resp = request_json(url, method="GET", headers=_auth_headers(key), timeout=60)
        except HttpRequestError as exc:
            _raise_opr_http_error(exc.status, (exc.body or "")[:300])
        if not isinstance(resp, dict):
            raise RuntimeError("Open PageRank returned a non-object response.")
        for row in resp.get("response") or []:
            if not isinstance(row, dict):
                continue
            domain = (row.get("domain") or "").strip().lower()
            if not domain:
                continue
            # status_code 200 means the domain is in the index; 404 means unknown.
            if int(row.get("status_code") or 0) != 200:
                continue
            raw = row.get("page_rank_decimal")
            if raw in (None, "", "N/A"):
                continue
            try:
                authority = float(raw)
            except (TypeError, ValueError):
                continue
            try:
                rank = int(row.get("rank")) if row.get("rank") not in (None, "", "N/A") else None
            except (TypeError, ValueError):
                rank = None
            out[domain] = {"authority": authority, "rank": rank}
        if idx < len(batches) - 1:
            time.sleep(OPR_BATCH_DELAY_SEC)
    return out


def _raise_opr_http_error(status: int | None, body: str) -> None:
    if status == 401:
        raise RuntimeError(
            "Open PageRank: authentication failed (401). Check the API key in Settings > Integrations."
        )
    if status == 403:
        raise RuntimeError(f"Open PageRank: forbidden (403). {body}")
    if status == 429:
        raise RuntimeError(
            "Open PageRank: rate limit or monthly quota exceeded (429). The free tier allows "
            "30,000 domains/month at 60 requests/minute."
        )
    raise RuntimeError(f"Open PageRank HTTP error ({status or '?'}): {body}") from None


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
               SET authority_score = ?, authority_rank = ?, authority_updated_at = ?
             WHERE domain = ?
            """,
            (
                hit["authority"] if hit else None,
                hit["rank"] if hit else None,
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
