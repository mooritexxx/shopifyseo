"""Tests for resolve_idea_targets — primary + secondary interlink resolution."""
import sqlite3

import pytest

from shopifyseo.dashboard_article_ideas import resolve_idea_targets
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    # Seed a few store objects used in the fixtures.
    for handle, title in [("disposable-vapes", "Disposable Vapes"), ("vape-kits", "Vape Kits")]:
        conn.execute(
            "INSERT INTO collections (handle, title, raw_json, synced_at) VALUES (?, ?, '{}', '')",
            (handle, title),
        )
    for handle, title in [("elfbar-bc5000", "Elfbar BC5000"), ("lost-mary-os5000", "Lost Mary OS5000")]:
        conn.execute(
            "INSERT INTO products (handle, title, status, tags_json, options_json, raw_json, synced_at) "
            "VALUES (?, ?, 'ACTIVE', '[]', '[]', '{}', '')",
            (handle, title),
        )
    conn.commit()
    return conn


def _seed_kpm(conn, rows):
    """rows: list of (keyword, object_type, object_handle, gsc_position)."""
    for kw, ot, oh, pos in rows:
        conn.execute(
            "INSERT INTO keyword_page_map (keyword, object_type, object_handle, gsc_position, source, updated_at) "
            "VALUES (?, ?, ?, ?, 'test', 0)",
            (kw, ot, oh, pos),
        )
    conn.commit()


def test_primary_comes_from_cluster_match_when_set(db_conn):
    cluster_meta = {
        "primary_keyword": "disposable vapes",
        "match_type": "collection",
        "match_handle": "disposable-vapes",
        "match_title": "Disposable Vapes",
        "top_keywords": [],
    }
    primary, secondary = resolve_idea_targets(db_conn, cluster_meta)
    assert primary["type"] == "collection"
    assert primary["handle"] == "disposable-vapes"
    assert primary["title"] == "Disposable Vapes"
    assert primary["source"] == "cluster_match"
    assert secondary == []


def test_primary_falls_back_to_existing_page_when_no_match(db_conn):
    cluster_meta = {
        "primary_keyword": "best disposable vapes",
        "match_type": "new",
        "match_handle": "",
        "match_title": "",
        "existing_page": {"object_type": "product", "object_handle": "elfbar-bc5000"},
        "top_keywords": [],
    }
    primary, _ = resolve_idea_targets(db_conn, cluster_meta)
    assert primary["type"] == "product"
    assert primary["handle"] == "elfbar-bc5000"
    assert primary["source"] == "existing_page"


def test_primary_falls_back_to_linked_collection_last(db_conn):
    cluster_meta = {
        "primary_keyword": "vape kits",
        "match_type": "",
        "match_handle": "",
        "match_title": "",
        "top_keywords": [],
    }
    primary, _ = resolve_idea_targets(
        db_conn,
        cluster_meta,
        linked_collection_handle="vape-kits",
        linked_collection_title="Vape Kits",
    )
    assert primary["type"] == "collection"
    assert primary["handle"] == "vape-kits"
    assert primary["source"] == "linked_collection"


def test_primary_empty_when_nothing_resolves(db_conn):
    cluster_meta = {
        "primary_keyword": "whatever",
        "match_type": "",
        "match_handle": "",
        "top_keywords": [],
    }
    primary, secondary = resolve_idea_targets(db_conn, cluster_meta)
    assert primary == {}
    assert secondary == []


def test_secondary_built_from_keyword_page_map_with_anchors(db_conn):
    _seed_kpm(
        db_conn,
        [
            ("disposable vapes", "collection", "disposable-vapes", 3.5),
            ("best disposable vape", "product", "elfbar-bc5000", 5.0),
            ("lost mary review", "product", "lost-mary-os5000", 7.0),
        ],
    )
    cluster_meta = {
        "primary_keyword": "disposable vapes",
        "match_type": "collection",
        "match_handle": "disposable-vapes",
        "match_title": "Disposable Vapes",
        "top_keywords": [
            {"keyword": "best disposable vape"},
            {"keyword": "lost mary review"},
        ],
    }
    primary, secondary = resolve_idea_targets(db_conn, cluster_meta, max_secondary=5)
    # primary dedupes itself from secondary
    handles = {(s["type"], s["handle"]) for s in secondary}
    assert ("collection", "disposable-vapes") not in handles
    assert ("product", "elfbar-bc5000") in handles
    assert ("product", "lost-mary-os5000") in handles
    by_handle = {s["handle"]: s for s in secondary}
    assert by_handle["elfbar-bc5000"]["anchor_keyword"] == "best disposable vape"
    assert by_handle["lost-mary-os5000"]["anchor_keyword"] == "lost mary review"


def test_secondary_capped_at_max(db_conn):
    _seed_kpm(
        db_conn,
        [
            ("kw1", "product", "elfbar-bc5000", 3.0),
            ("kw2", "product", "lost-mary-os5000", 3.0),
            ("kw3", "collection", "vape-kits", 3.0),
        ],
    )
    cluster_meta = {
        "primary_keyword": "p",
        "match_type": "collection",
        "match_handle": "disposable-vapes",
        "match_title": "Disposable Vapes",
        "top_keywords": [
            {"keyword": "kw1"},
            {"keyword": "kw2"},
            {"keyword": "kw3"},
        ],
    }
    _, secondary = resolve_idea_targets(db_conn, cluster_meta, max_secondary=2)
    assert len(secondary) == 2
