"""Competitor domain blocklist management."""

import json
import sqlite3

from shopifyseo.dashboard_google import get_service_setting, set_service_setting

COMPETITOR_BLOCKLIST_KEY = "competitor_domain_blocklist"
# JSON object: normalized domain -> last known profile metrics (shown on Dismissed tab after reject/delete).
COMPETITOR_DISMISSED_SNAPSHOTS_KEY = "competitor_dismissed_snapshots"

# Domains competitor-discovery APIs may return that are not retail SEO peers (UGC, search, social).
DISCOVERY_SKIP_DOMAINS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "google.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "reddit.com",
        "wikipedia.org",
        "pinterest.com",
        "tiktok.com",
    }
)


def norm_competitor_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


def load_competitor_blocklist(conn: sqlite3.Connection) -> set[str]:
    raw = get_service_setting(conn, COMPETITOR_BLOCKLIST_KEY, "[]")
    try:
        return {norm_competitor_domain(x) for x in json.loads(raw) if x}
    except json.JSONDecodeError:
        return set()


def add_competitor_to_blocklist(conn: sqlite3.Connection, domain: str) -> None:
    n = norm_competitor_domain(domain)
    if not n:
        return
    b = load_competitor_blocklist(conn)
    if n not in b:
        b.add(n)
        set_service_setting(conn, COMPETITOR_BLOCKLIST_KEY, json.dumps(sorted(b)))


def load_dismissed_snapshots(conn: sqlite3.Connection) -> dict[str, dict]:
    raw = get_service_setting(conn, COMPETITOR_DISMISSED_SNAPSHOTS_KEY, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in data.items():
        nk = norm_competitor_domain(str(k))
        if nk and isinstance(v, dict):
            out[nk] = v
    return out


def _save_dismissed_snapshots(conn: sqlite3.Connection, snapshots: dict[str, dict]) -> None:
    set_service_setting(conn, COMPETITOR_DISMISSED_SNAPSHOTS_KEY, json.dumps(snapshots))


def remove_dismissed_snapshot(conn: sqlite3.Connection, domain: str) -> None:
    n = norm_competitor_domain(domain)
    if not n:
        return
    snaps = load_dismissed_snapshots(conn)
    if n in snaps:
        del snaps[n]
        _save_dismissed_snapshots(conn, snaps)


def upsert_dismissed_profile_snapshot(conn: sqlite3.Connection, snapshot: dict) -> None:
    """Store metrics for a domain being dismissed so the Dismissed tab can show Traffic / Common / Gap."""
    dom = norm_competitor_domain(str(snapshot.get("domain", "")))
    if not dom:
        return
    snaps = load_dismissed_snapshots(conn)
    snaps[dom] = {
        "domain": dom,
        "keywords_common": int(snapshot.get("keywords_common") or 0),
        "keywords_they_have": int(snapshot.get("keywords_they_have") or 0),
        "keywords_we_have": int(snapshot.get("keywords_we_have") or 0),
        "share": float(snapshot.get("share") or 0.0),
        "traffic": int(snapshot.get("traffic") or 0),
        "labs_visibility": float(snapshot.get("labs_visibility") or 0.0),
        "labs_avg_position": int(snapshot.get("labs_avg_position") or 0),
        "labs_median_position": int(snapshot.get("labs_median_position") or 0),
        "labs_seed_etv": int(snapshot.get("labs_seed_etv") or 0),
        "labs_bulk_etv": int(snapshot.get("labs_bulk_etv") or 0),
        "labs_rating": int(snapshot.get("labs_rating") or 0),
        "is_manual": int(snapshot.get("is_manual") or 0),
        "updated_at": int(snapshot.get("updated_at") or 0),
    }
    _save_dismissed_snapshots(conn, snaps)


def remove_competitor_from_blocklist(conn: sqlite3.Connection, domain: str) -> None:
    n = norm_competitor_domain(domain)
    b = load_competitor_blocklist(conn)
    if n in b:
        b.discard(n)
        set_service_setting(conn, COMPETITOR_BLOCKLIST_KEY, json.dumps(sorted(b)))
    remove_dismissed_snapshot(conn, n)


def competitor_domain_allowed_for_research(conn: sqlite3.Connection, domain: str) -> bool:
    n = norm_competitor_domain(domain)
    if not n:
        return False
    if n in DISCOVERY_SKIP_DOMAINS:
        return False
    if n in load_competitor_blocklist(conn):
        return False
    return True


def purge_disallowed_competitor_rows(conn: sqlite3.Connection) -> None:
    """Remove DB rows for blocklisted or non-retail competitor domains (e.g. youtube.com)."""
    banned = list(load_competitor_blocklist(conn) | DISCOVERY_SKIP_DOMAINS)
    if not banned:
        return
    placeholders = ",".join("?" for _ in banned)
    conn.execute(f"DELETE FROM competitor_profiles WHERE domain IN ({placeholders})", banned)
    conn.execute(f"DELETE FROM competitor_top_pages WHERE competitor_domain IN ({placeholders})", banned)
    conn.execute(f"DELETE FROM competitor_keyword_gaps WHERE competitor_domain IN ({placeholders})", banned)
    conn.commit()
