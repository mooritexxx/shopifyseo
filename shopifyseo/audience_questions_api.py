"""SerpAPI: Google Search snapshot for article idea primary keywords.

Uses SerpAPI’s **Google Search** JSON API (`search.json`, ``engine=google``) once per
keyword (no separate ``google_related_questions`` call). Passes **localization**
from Settings → Primary market (``gl``, ``hl``, ``google_domain`` via
``shopifyseo.market_context``) and reads:

- ``related_questions`` (People Also Ask) → ``audience_questions`` as
  ``[{question, snippet}, ...]`` using each item’s ``question`` and ``snippet``
  only (the same preview text Google exposes on the first SERP).
- ``organic_results`` → ``top_ranking_pages`` as ``[{title, url}, ...]``.
- ``ai_overview`` (when present) → stored subset: ``text_blocks`` (paragraph / list)
  plus ``references`` (title, link, snippet, source, index).
- ``related_searches`` → ``[{query, position}, ...]`` using each item’s ``query`` and
  ``position`` when SerpAPI provides it; otherwise position is the 1-based index in the list.

Requires **SerpAPI API key** saved in Settings → Integrations → SerpAPI
(service setting ``serpapi_api_key``).

Optional: ``RELATED_QUESTIONS_DELAY_SEC`` — seconds to sleep between SerpAPI
calls when generating a batch of ideas (rate limiting).

When ``expand_paa=True`` (article idea **Refresh SERP data** only), after the
initial ``engine=google`` response we call SerpAPI ``engine=google_related_questions``
for each top-level ``related_questions`` item that includes a ``next_page_token``,
up to ``PAA_EXPANSION_MAX_PARENTS`` (default 6). Each parent is paginated to collect
up to ``PAA_EXPANSION_MAX_CHILDREN`` (default 10) sub-questions, following
``next_page_token`` on the last item and optional same-token refetches (``PAA_SAME_TOKEN_EXTRA_ROUNDS``).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import shopifyseo.dashboard_google as dg
import shopifyseo.market_context as mc

logger = logging.getLogger(__name__)

SERPAPI_SEARCH_JSON = "https://serpapi.com/search.json"

# Fixed test query for Settings → Test connection (generic informational keyword).
SERPAPI_SETTINGS_TEST_KEYWORD = "black coffee"

# Cap organic rows stored per idea (first page only).
_MAX_ORGANIC_RESULTS = 15

# SerpAPI Google `engine=google`: low `num` is linked to sporadic empty / error-like responses;
# 10+ is a common stable default.
_SERPAPI_GOOGLE_NUM_RESULTS = "10"

# If primary-market Google returns the common “no results” error and no blocks to parse, retry
# once on google.com — some queries (e.g. short head terms) return nothing on a regional TLD
# but do return PAA/organics in the US index.
_SERPAPI_US_FALLBACK: dict[str, str] = {"gl": "us", "hl": "en", "google_domain": "google.com"}

# Cap ``related_searches`` rows per idea.
_MAX_RELATED_SEARCHES = 40

# PAA expansion (SerpAPI ``google_related_questions``) after the main Google search.
PAA_EXPANSION_MAX_PARENTS_DEFAULT = 6
PAA_EXPANSION_MAX_CHILDREN_DEFAULT = 10
PAA_EXPANSION_DELAY_SEC_DEFAULT = 0.35
# Extra requests with the same next_page_token can return more rows (see SerpAPI blog, PAA pagination).
PAA_SAME_TOKEN_EXTRA_ROUNDS_DEFAULT = 1

# Cap AI overview payload size (SerpAPI shape varies; keep JSON bounded).
_MAX_AI_OVERVIEW_BLOCKS = 48
_MAX_AI_OVERVIEW_LIST_ITEMS = 40
_MAX_AI_OVERVIEW_REFS = 40
_MAX_AI_SNIPPET_CHARS = 6000


def _trim_str(value: Any, max_len: int = _MAX_AI_SNIPPET_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    s = value.strip()
    return s if len(s) <= max_len else s[:max_len]


def _safe_reference_indexes(raw: Any, cap: int = 48) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            if isinstance(x, bool):
                continue
            if isinstance(x, int):
                out.append(x)
            elif isinstance(x, float) and x.is_integer():
                out.append(int(x))
            elif isinstance(x, str) and x.strip():
                out.append(int(float(x)))
        except (TypeError, ValueError, OverflowError):
            continue
        if len(out) >= cap:
            break
    return out


def _normalize_snippet_latex(raw: Any) -> list[str]:
    if isinstance(raw, str) and raw.strip():
        return [_trim_str(raw, 800)]
    if isinstance(raw, list):
        acc: list[str] = []
        for x in raw:
            if isinstance(x, str) and x.strip():
                acc.append(_trim_str(x, 800))
            if len(acc) >= 12:
                break
        return acc
    return []


def _ai_overview_text_block(item: dict[str, Any]) -> dict[str, Any] | None:
    typ = item.get("type")
    idxs = _safe_reference_indexes(item.get("reference_indexes"))
    if typ == "paragraph":
        sn = _trim_str(item.get("snippet"))
        if not sn and not idxs:
            return None
        block: dict[str, Any] = {"type": "paragraph", "snippet": sn}
        if idxs:
            block["reference_indexes"] = idxs
        return block
    if typ == "list":
        raw_list = item.get("list")
        if not isinstance(raw_list, list):
            return None
        cleaned: list[dict[str, Any]] = []
        for li in raw_list:
            if not isinstance(li, dict):
                continue
            entry: dict[str, Any] = {"snippet": _trim_str(li.get("snippet"))}
            latex = _normalize_snippet_latex(li.get("snippet_latex"))
            if latex:
                entry["snippet_latex"] = latex
            if entry["snippet"] or entry.get("snippet_latex"):
                cleaned.append(entry)
            if len(cleaned) >= _MAX_AI_OVERVIEW_LIST_ITEMS:
                break
        if not cleaned and not idxs:
            return None
        block = {"type": "list", "list": cleaned}
        if idxs:
            block["reference_indexes"] = idxs
        return block
    return None


def _ai_overview_from_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    """Copy SerpAPI ``ai_overview`` text_blocks + references only; bounded size."""
    aio = data.get("ai_overview")
    if not isinstance(aio, dict):
        return None
    out_blocks: list[dict[str, Any]] = []
    tbs = aio.get("text_blocks")
    if isinstance(tbs, list):
        for tb in tbs:
            if not isinstance(tb, dict):
                continue
            block = _ai_overview_text_block(tb)
            if block:
                out_blocks.append(block)
            if len(out_blocks) >= _MAX_AI_OVERVIEW_BLOCKS:
                break
    out_refs: list[dict[str, Any]] = []
    refs = aio.get("references")
    if isinstance(refs, list):
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            link = str(ref.get("link") or "").strip()
            if not link:
                continue
            try:
                ri = int(ref.get("index")) if ref.get("index") is not None else len(out_refs)
            except (TypeError, ValueError):
                ri = len(out_refs)
            out_refs.append(
                {
                    "title": _trim_str(ref.get("title"), 500),
                    "link": link[:2048],
                    "snippet": _trim_str(ref.get("snippet"), 800),
                    "source": _trim_str(ref.get("source"), 240),
                    "index": ri,
                }
            )
            if len(out_refs) >= _MAX_AI_OVERVIEW_REFS:
                break
    if not out_blocks and not out_refs:
        return None
    out: dict[str, Any] = {}
    if out_blocks:
        out["text_blocks"] = out_blocks
    if out_refs:
        out["references"] = out_refs
    return out


def _snippet_from_related_item(item: dict[str, Any]) -> str:
    """PAA preview from SerpAPI ``related_questions`` item: Google Search ``snippet`` only."""
    sn = item.get("snippet")
    return sn.strip() if isinstance(sn, str) else ""


def _qa_from_related_payload(data: dict[str, Any]) -> list[dict[str, str]]:
    """Build ``[{question, snippet}, ...]`` from SerpAPI ``related_questions`` on the Google search JSON."""
    out: list[dict[str, str]] = []
    rq = data.get("related_questions")
    if not isinstance(rq, list):
        return out
    for item in rq:
        if isinstance(item, str) and item.strip():
            out.append({"question": item.strip(), "snippet": ""})
        elif isinstance(item, dict):
            q = item.get("question")
            if isinstance(q, str) and q.strip():
                out.append({"question": q.strip(), "snippet": _snippet_from_related_item(item)})
        if len(out) >= 80:
            break
    return out


def _top_organic_pages_from_payload(data: dict[str, Any]) -> list[dict[str, str]]:
    """Build ``[{title, url}, ...]`` from SerpAPI ``organic_results`` (``link`` → ``url``)."""
    out: list[dict[str, str]] = []
    org = data.get("organic_results")
    if not isinstance(org, list):
        return out
    for item in org:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = item.get("link") or item.get("url") or ""
        url = link.strip() if isinstance(link, str) else ""
        if not url:
            continue
        if not title:
            title = url if len(url) <= 120 else url[:117] + "…"
        out.append({"title": title, "url": url})
        if len(out) >= _MAX_ORGANIC_RESULTS:
            break
    return out


def _related_search_position(entry: dict[str, Any], fallback: int) -> int:
    """SerpAPI ``position`` on a related_searches item when present; else *fallback* (1-based index)."""
    pos = entry.get("position")
    if isinstance(pos, bool):
        return fallback
    if isinstance(pos, int):
        return pos
    if isinstance(pos, float) and pos.is_integer():
        return int(pos)
    if isinstance(pos, str) and pos.strip():
        try:
            return int(float(pos))
        except ValueError:
            pass
    return fallback


def _serpapi_payload_has_usable_features(data: dict[str, Any]) -> bool:
    """True if the JSON has at least one block we store (PAA, organics, AI overview, related searches)."""
    if _qa_from_related_payload(data):
        return True
    if _top_organic_pages_from_payload(data):
        return True
    if _ai_overview_from_payload(data):
        return True
    if _related_searches_from_payload(data):
        return True
    return False


def _is_serpapi_organic_empty_noise(err: str) -> bool:
    """SerpAPI sometimes sets this when organic is empty or parsing glitches, while PAA still exists."""
    el = err.strip().lower()
    return "hasn't returned any results" in el or "has not returned any results" in el


def _related_searches_from_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build ``[{query, position}, ...]`` from SerpAPI ``related_searches``."""
    raw = data.get("related_searches")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, entry in enumerate(raw):
        fallback = i + 1
        if isinstance(entry, str):
            q = entry.strip()
            if q:
                out.append({"query": q, "position": fallback})
        elif isinstance(entry, dict):
            q = str(entry.get("query") or "").strip()
            if not q:
                continue
            out.append({"query": q, "position": _related_search_position(entry, fallback)})
        if len(out) >= _MAX_RELATED_SEARCHES:
            break
    return out


def _paa_next_page_token_from_expansion(
    child_payload: dict[str, Any], request_token: str
) -> str | None:
    """Next ``next_page_token`` to load more sub-questions for the same expanded parent.

    Google typically adds ~2 at a time; SerpAPI exposes the next step on the last
    ``related_questions`` item. Skip if it equals *request_token* to avoid a tight loop.
    """
    req = (request_token or "").strip()
    rq = child_payload.get("related_questions")
    if not isinstance(rq, list) or not rq:
        return None
    last = rq[-1]
    if not isinstance(last, dict):
        return None
    t = last.get("next_page_token")
    if not isinstance(t, str) or not t.strip():
        return None
    ts = t.strip()
    if ts == req:
        return None
    return ts


def _fetch_google_related_questions_expansion(
    api_key: str,
    next_page_token: str,
    localization: dict[str, str],
) -> dict[str, Any] | None:
    """One SerpAPI ``engine=google_related_questions`` call (deeper PAA for a parent question)."""
    key = (api_key or "").strip()
    tok = (next_page_token or "").strip()
    if not key or not tok:
        return None
    params: dict[str, str] = {
        "engine": "google_related_questions",
        "next_page_token": tok,
        "api_key": key,
    }
    if localization:
        for lk, lv in localization.items():
            if isinstance(lv, str) and lv.strip():
                params[lk] = lv.strip()
    url = SERPAPI_SEARCH_JSON + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "ShopifySEO/1.0 (article-ideas)"}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:  # noqa: S310 — SerpAPI HTTPS
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        err = data.get("error")
        if isinstance(err, str) and err.strip():
            logger.warning("SerpAPI google_related_questions error: %s", err.strip())
            return None
        return data
    except HTTPError as exc:
        logger.warning("SerpAPI google_related_questions HTTP %s", exc.code, exc_info=True)
        return None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("SerpAPI google_related_questions request failed: %s", exc)
        return None


def _collect_paa_children_for_one_parent(
    api_key: str,
    first_token: str,
    parent_question: str,
    localization: dict[str, str],
    delay: float,
    max_children: int,
) -> list[dict[str, str]]:
    """Follow ``next_page_token`` pagination; optional extra round with same first token (SerpAPI PAA blog)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    first = (first_token or "").strip()
    if not first:
        return out
    try:
        extra_same = int(
            os.environ.get("PAA_SAME_TOKEN_EXTRA_ROUNDS", str(PAA_SAME_TOKEN_EXTRA_ROUNDS_DEFAULT))
        )
    except (TypeError, ValueError):
        extra_same = PAA_SAME_TOKEN_EXTRA_ROUNDS_DEFAULT
    extra_same = max(0, min(extra_same, 4))
    try:
        max_req = int(os.environ.get("PAA_EXPANSION_MAX_REQUESTS_PER_PARENT", "18"))
    except (TypeError, ValueError):
        max_req = 18
    max_req = max(1, min(max_req, 40))

    token = first
    same_again: dict[str, int] = {}
    for req_i in range(max_req):
        if len(out) >= max_children:
            break
        if req_i > 0 and delay > 0:
            time.sleep(delay)
        n_before = len(out)
        child_payload = _fetch_google_related_questions_expansion(api_key, token, localization)
        if not child_payload:
            break
        for row in _qa_from_related_payload(child_payload):
            k = (row.get("question") or "").strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(row)
            if len(out) >= max_children:
                return out
        nxt = _paa_next_page_token_from_expansion(child_payload, token)
        if nxt:
            token = nxt
            continue
        if len(out) == n_before:
            break
        st = same_again.get(token, 0)
        if st < extra_same and len(out) < max_children:
            same_again[token] = st + 1
            continue
        break
    if not out and (parent_question or "").strip():
        logger.debug(
            "PAA expansion: no sub-questions after pagination for parent %r.",
            (parent_question or "")[:80],
        )
    return out


def expand_paa_via_related_questions_engine(
    api_key: str,
    initial_serp_data: dict[str, Any],
    localization: dict[str, str],
) -> list[dict[str, Any]]:
    """For each top PAA row with ``next_page_token``, fetch child questions via ``google_related_questions``."""
    try:
        max_parents = int(os.environ.get("PAA_EXPANSION_MAX_PARENTS", str(PAA_EXPANSION_MAX_PARENTS_DEFAULT)))
    except (TypeError, ValueError):
        max_parents = PAA_EXPANSION_MAX_PARENTS_DEFAULT
    max_parents = max(0, min(max_parents, 20))
    try:
        max_children = int(
            os.environ.get("PAA_EXPANSION_MAX_CHILDREN", str(PAA_EXPANSION_MAX_CHILDREN_DEFAULT))
        )
    except (TypeError, ValueError):
        max_children = PAA_EXPANSION_MAX_CHILDREN_DEFAULT
    max_children = max(1, min(max_children, 20))
    try:
        delay = float(os.environ.get("PAA_EXPANSION_DELAY_SEC", str(PAA_EXPANSION_DELAY_SEC_DEFAULT)))
    except (TypeError, ValueError):
        delay = PAA_EXPANSION_DELAY_SEC_DEFAULT
    delay = max(0.0, min(delay, 5.0))

    layers: list[dict[str, Any]] = []
    rq = initial_serp_data.get("related_questions")
    if not isinstance(rq, list):
        return layers

    for i, item in enumerate(rq):
        if len(layers) >= max_parents:
            break
        if not isinstance(item, dict):
            continue
        q = item.get("question")
        if not isinstance(q, str) or not q.strip():
            continue
        token = item.get("next_page_token")
        if not isinstance(token, str) or not token.strip():
            continue
        if i > 0 and delay > 0:
            time.sleep(delay)
        children = _collect_paa_children_for_one_parent(
            api_key, token, q.strip(), localization, delay, max_children
        )
        if children:
            layers.append({"parent_question": q.strip(), "children": children})
    return layers


def _serpapi_one_google_organic_request(
    api_key: str,
    keyword: str,
    localization: dict[str, str] | None,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, Any] | None,
    list[dict[str, Any]],
    str | None,
    dict[str, Any] | None,
]:
    """Single ``engine=google`` HTTP call; return parsed fields, error, raw JSON (or None on hard error)."""
    kw = (keyword or "").strip()
    key = (api_key or "").strip()
    if not key:
        return [], [], None, [], "SerpAPI API key is empty.", None
    if not kw:
        return [], [], None, [], "Keyword is empty.", None

    params: dict[str, str] = {
        "engine": "google",
        "q": kw,
        "api_key": key,
        "num": _SERPAPI_GOOGLE_NUM_RESULTS,
    }
    if localization:
        for lk, lv in localization.items():
            if isinstance(lv, str) and lv.strip():
                params[lk] = lv.strip()
    url = SERPAPI_SEARCH_JSON + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "ShopifySEO/1.0 (article-ideas)"}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:  # noqa: S310 — SerpAPI HTTPS
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return [], [], None, [], "SerpAPI returned an unexpected JSON shape.", None
        err: str | None = None
        raw_err = data.get("error")
        if isinstance(raw_err, str) and raw_err.strip():
            e = raw_err.strip()
            if _is_serpapi_organic_empty_noise(e) and _serpapi_payload_has_usable_features(data):
                logger.info(
                    "SerpAPI reported %r for keyword %r but SERP features are present; continuing.",
                    e,
                    kw,
                )
            else:
                err = e
        if err:
            logger.warning("SerpAPI error for keyword %r: %s", kw, err)
            return [], [], None, [], err, None
        aio = _ai_overview_from_payload(data)
        rel = _related_searches_from_payload(data)
        return (
            _qa_from_related_payload(data),
            _top_organic_pages_from_payload(data),
            aio,
            rel,
            None,
            data,
        )
    except HTTPError as exc:
        detail = f"HTTP {exc.code}"
        try:
            body = exc.read().decode("utf-8", errors="replace")
            err_obj = json.loads(body)
            if isinstance(err_obj, dict):
                em = err_obj.get("error")
                if isinstance(em, str) and em.strip():
                    return [], [], None, [], f"{detail}: {em.strip()}", None
        except Exception:
            pass
        reason = getattr(exc, "reason", None) or str(exc)
        return [], [], None, [], f"SerpAPI request failed ({detail}: {reason}).", None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("SerpAPI Google search request failed for keyword %r: %s", kw, exc)
        return [], [], None, [], str(exc) or "SerpAPI request failed.", None


def _serpapi_fetch_google_serp_snapshot(
    api_key: str,
    keyword: str,
    *,
    localization: dict[str, str] | None = None,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[str, Any] | None,
    list[dict[str, Any]],
    str | None,
    dict[str, Any] | None,
    dict[str, str],
]:
    """Call SerpAPI (and optionally a US index fallback); return parsed fields, error, raw JSON, **loc used** for PAA.

    PAA expansion must use the same ``gl``/domain as the main response that provided ``next_page_token``s.
    """
    loc_primary = {k: v for k, v in (localization or {}).items() if isinstance(v, str) and v.strip()}
    r1 = _serpapi_one_google_organic_request(api_key, keyword, loc_primary)
    if r1[4] is None:
        return (*r1, loc_primary)

    err1 = r1[4]
    gl0 = (loc_primary.get("gl") or "").lower()
    if _is_serpapi_organic_empty_noise(err1) and gl0 != "us":
        r2 = _serpapi_one_google_organic_request(
            api_key, keyword, dict(_SERPAPI_US_FALLBACK)
        )
        if r2[4] is None:
            logger.info(
                "SERP: primary market returned no parseable index for %r; using US Google (google.com) for this run.",
                (keyword or "").strip(),
            )
            return (*r2, dict(_SERPAPI_US_FALLBACK))
    return (*r1, loc_primary)


def fetch_serpapi_primary_keyword_snapshot(
    conn: sqlite3.Connection,
    keyword: str,
    *,
    expand_paa: bool = False,
) -> dict[str, Any]:
    """Return SerpAPI Google search fields for one keyword; empty lists / None on skip/error.

    ``expand_paa`` runs extra ``google_related_questions`` calls (uses SerpAPI credits).
    """
    empty: dict[str, Any] = {
        "audience_questions": [],
        "top_ranking_pages": [],
        "ai_overview": None,
        "related_searches": [],
        "paa_expansion": [],
    }
    kw = (keyword or "").strip()
    if not kw:
        return empty
    api_key = (dg.get_service_setting(conn, "serpapi_api_key") or "").strip()
    if not api_key:
        return empty
    loc = mc.serpapi_google_search_params(conn)
    qa, pages, aio, rel, err, raw_data, loc_effective = _serpapi_fetch_google_serp_snapshot(
        api_key, kw, localization=loc
    )
    if err:
        logger.debug("SerpAPI snapshot skipped: %s", err)
        # So callers (e.g. refresh SERP) can surface a failure instead of saving empty JSON.
        return {**empty, "serpapi_error": err}
    out: dict[str, Any] = {
        "audience_questions": qa,
        "top_ranking_pages": pages,
        "ai_overview": aio,
        "related_searches": rel,
        "paa_expansion": [],
    }
    if expand_paa and isinstance(raw_data, dict):
        try:
            out["paa_expansion"] = expand_paa_via_related_questions_engine(
                api_key, raw_data, loc_effective
            )
        except Exception:
            logger.warning("SerpAPI PAA expansion failed (non-fatal)", exc_info=True)
            out["paa_expansion"] = []
    return out


def fetch_related_questions_serpapi(conn: sqlite3.Connection, keyword: str) -> list[dict[str, str]]:
    """Return ``[{question, snippet}, ...]`` only (same single Google search as full snapshot)."""
    return fetch_serpapi_primary_keyword_snapshot(conn, keyword)["audience_questions"]


def run_serpapi_connection_test(
    conn: sqlite3.Connection,
    *,
    api_key_override: str = "",
    test_keyword: str = SERPAPI_SETTINGS_TEST_KEYWORD,
) -> dict[str, Any]:
    """Settings UI: verify API key with a fixed test query.

    Uses *api_key_override* when non-empty after strip; otherwise reads ``serpapi_api_key`` from DB.
    """
    key = (api_key_override or "").strip() or (dg.get_service_setting(conn, "serpapi_api_key") or "").strip()
    kw = (test_keyword or SERPAPI_SETTINGS_TEST_KEYWORD).strip() or SERPAPI_SETTINGS_TEST_KEYWORD
    loc = mc.serpapi_google_search_params(conn)
    qa, pages, aio, rel, err, _raw, _loc = _serpapi_fetch_google_serp_snapshot(
        key, kw, localization=loc
    )
    if err:
        return {
            "ok": False,
            "detail": err,
            "question_count": 0,
            "organic_count": 0,
            "has_ai_overview": False,
            "related_search_count": 0,
            "questions": [],
            "items": [],
            "organic_pages": [],
        }
    nq = len(qa)
    no = len(pages)
    nrel = len(rel)
    has_ai = bool(aio and (aio.get("text_blocks") or aio.get("references")))
    preview_qs = [x["question"] for x in qa[:3]]
    preview = "; ".join(preview_qs) if preview_qs else "(no related questions in this response)"
    organic_hint = f"{no} organic listing(s)" if no else "no organic block in this response"
    rel_hint = f"{nrel} related search(es)" if nrel else "no related searches block"
    return {
        "ok": True,
        "detail": f"SerpAPI OK — {nq} related question(s), {organic_hint}, {rel_hint}"
        + (", AI overview present" if has_ai else ", no AI overview in this response")
        + f' for test query “{kw}”. Questions: {preview}',
        "question_count": nq,
        "organic_count": no,
        "has_ai_overview": has_ai,
        "related_search_count": nrel,
        "questions": [x["question"] for x in qa[:10]],
        "items": qa[:10],
        "organic_pages": pages[:10],
        "related_searches": rel[:12],
    }


def enrich_article_ideas_with_audience_questions(
    conn: sqlite3.Connection,
    ideas: list[dict[str, Any]],
) -> None:
    """Mutate each idea with SerpAPI Google search fields (PAA, organics, AI overview, related searches)."""
    try:
        delay = float(os.environ.get("RELATED_QUESTIONS_DELAY_SEC") or "0")
    except (TypeError, ValueError):
        delay = 0.0

    for i, idea in enumerate(ideas):
        pk = str(idea.get("primary_keyword") or "").strip()
        if not pk:
            idea["audience_questions"] = []
            idea["top_ranking_pages"] = []
            idea["ai_overview"] = None
            idea["related_searches"] = []
            idea["paa_expansion"] = []
        else:
            snap = fetch_serpapi_primary_keyword_snapshot(conn, pk, expand_paa=False)
            if isinstance(snap, dict):
                snap.pop("serpapi_error", None)
            idea["audience_questions"] = snap["audience_questions"]
            idea["top_ranking_pages"] = snap["top_ranking_pages"]
            idea["ai_overview"] = snap.get("ai_overview")
            idea["related_searches"] = snap.get("related_searches") or []
            idea["paa_expansion"] = snap.get("paa_expansion") or []
        if delay > 0 and i + 1 < len(ideas):
            time.sleep(delay)
