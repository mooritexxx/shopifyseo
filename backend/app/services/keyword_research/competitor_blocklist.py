"""Competitor domain blocklist management."""

import json
import sqlite3

from shopifyseo.dashboard_google import get_service_setting, set_service_setting

COMPETITOR_BLOCKLIST_KEY = "competitor_domain_blocklist"

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


def remove_competitor_from_blocklist(conn: sqlite3.Connection, domain: str) -> None:
    n = norm_competitor_domain(domain)
    b = load_competitor_blocklist(conn)
    if n in b:
        b.discard(n)
        set_service_setting(conn, COMPETITOR_BLOCKLIST_KEY, json.dumps(sorted(b)))


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
