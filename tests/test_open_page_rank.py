"""Open PageRank client — domain authority for competitor profiles."""

import sqlite3

import pytest

from backend.app.services import open_page_rank as opr
from shopifyseo.dashboard_store import ensure_dashboard_schema


def _fake_response(rows):
    return {"as_of": "2026-06-01", "count": len(rows), "results": rows}


def test_fetch_domain_authority_parses_scores(monkeypatch):
    captured = {}

    def fake_request_json(url, **kwargs):
        captured["url"] = url
        captured["method"] = kwargs.get("method")
        captured["headers"] = kwargs.get("headers")
        captured["payload"] = kwargs.get("payload")
        return _fake_response(
            [
                {"domain": "a.com", "found": True, "open_page_rank": 5.42, "rank": 1200,
                 "referring_domains": 843},
                {"domain": "b.com", "found": True, "open_page_rank": 3, "rank": None,
                 "referring_domains": None},
            ]
        )

    monkeypatch.setattr(opr, "request_json", fake_request_json)
    out = opr.fetch_domain_authority("opr_live_key", ["A.com", "b.com"])

    assert out == {
        "a.com": {"authority": 5.42, "rank": 1200, "referring_domains": 843},
        "b.com": {"authority": 3.0, "rank": None, "referring_domains": None},
    }
    assert captured["url"] == opr.OPR_BULK_URL
    assert captured["method"] == "POST"
    assert captured["headers"]["Authorization"] == "Bearer opr_live_key"
    assert captured["payload"] == {"domains": ["a.com", "b.com"], "include_history": False}


def test_unindexed_domains_are_omitted_not_zero(monkeypatch):
    """A domain Open PageRank does not know is unknown, not authority 0."""

    def fake_request_json(url, **kwargs):
        return _fake_response(
            [
                {"domain": "known.com", "found": True, "open_page_rank": 4.1, "rank": 50,
                 "referring_domains": 12},
                {"domain": "unknown.com", "found": False, "open_page_rank": 0, "rank": None,
                 "referring_domains": None},
                {"domain": "blank.com", "found": True, "open_page_rank": "N/A", "rank": None,
                 "referring_domains": None},
            ]
        )

    monkeypatch.setattr(opr, "request_json", fake_request_json)
    out = opr.fetch_domain_authority("k", ["known.com", "unknown.com", "blank.com"])

    assert set(out) == {"known.com"}
    assert "unknown.com" not in out
    assert "blank.com" not in out


def test_fetch_domain_authority_requires_key():
    with pytest.raises(RuntimeError, match="API key is required"):
        opr.fetch_domain_authority("", ["a.com"])


def test_fetch_domain_authority_empty_input_skips_call(monkeypatch):
    def boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("no request should be made for an empty domain list")

    monkeypatch.setattr(opr, "request_json", boom)
    assert opr.fetch_domain_authority("k", []) == {}


def test_refresh_competitor_authority_writes_null_for_unknown(monkeypatch, tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    for dom in ("scored.ca", "unscored.ca"):
        conn.execute("INSERT INTO competitor_profiles (domain) VALUES (?)", (dom,))
    conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, ?)",
        (opr.OPR_SETTING_KEY, "opr_live_test"),
    )
    conn.commit()

    monkeypatch.setattr(
        opr,
        "fetch_domain_authority",
        lambda key, domains: {
            "scored.ca": {"authority": 6.5, "rank": 900, "referring_domains": 77}
        },
    )

    stats = opr.refresh_competitor_authority(conn)
    assert stats == {"checked": 2, "scored": 1, "unknown": 1}

    rows = {
        r["domain"]: r
        for r in conn.execute(
            "SELECT domain, authority_score, authority_rank, referring_domains"
            " FROM competitor_profiles"
        )
    }
    assert rows["scored.ca"]["authority_score"] == 6.5
    assert rows["scored.ca"]["authority_rank"] == 900
    assert rows["scored.ca"]["referring_domains"] == 77
    # Unknown stays NULL rather than becoming 0.
    assert rows["unscored.ca"]["authority_score"] is None
    assert rows["unscored.ca"]["authority_rank"] is None
    assert rows["unscored.ca"]["referring_domains"] is None
    conn.close()


def test_refresh_requires_key(tmp_path):
    conn = sqlite3.connect(tmp_path / "t2.sqlite3")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    conn.execute("INSERT INTO competitor_profiles (domain) VALUES ('x.ca')")
    conn.commit()
    with pytest.raises(RuntimeError, match="API key is required"):
        opr.refresh_competitor_authority(conn)
    conn.close()


# --- storefront's own authority ---


def _site_conn(tmp_path, name, **settings):
    conn = sqlite3.connect(tmp_path / name)
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    for k, v in settings.items():
        conn.execute("INSERT INTO service_settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    return conn


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({"store_custom_domain": "https://www.Vapely.ca/"}, "vapely.ca"),
        ({"store_custom_domain": "", "shopify_shop": "x-1.myshopify.com"}, "x-1.myshopify.com"),
        ({}, ""),
    ],
)
def test_resolve_site_domain(tmp_path, settings, expected):
    conn = _site_conn(tmp_path, f"r{abs(hash(expected))}.sqlite3", **settings)
    assert opr.resolve_site_domain(conn) == expected
    conn.close()


def test_fetch_site_authority_returns_sorted_history(monkeypatch):
    monkeypatch.setattr(
        opr,
        "request_json",
        lambda url, **kw: {
            "as_of": "2026-07-01",
            "results": [
                {
                    "domain": "d.ca", "found": True, "open_page_rank": 4.2, "rank": 130,
                    "referring_domains": 96,
                    "history": [
                        {"date": "2026-07-01", "open_page_rank": 4.2, "estimated": False},
                        {"date": "2018-01-01", "open_page_rank": 3.1, "estimated": True},
                    ],
                }
            ],
        },
    )
    out = opr.fetch_site_authority("k", "D.ca")
    assert out["found"] is True
    assert out["authority"] == 4.2
    assert out["as_of"] == "2026-07-01"
    assert [p["date"] for p in out["history"]] == ["2018-01-01", "2026-07-01"]
    assert out["history"][0]["estimated"] is True


def test_unindexed_site_reports_not_found_not_zero(monkeypatch, tmp_path):
    """An unindexed storefront has no score — it must not be stored as 0."""
    conn = _site_conn(
        tmp_path, "site1.sqlite3",
        store_custom_domain="vapely.ca",
        open_page_rank_api_key="opr_live_x",
    )
    monkeypatch.setattr(
        opr,
        "request_json",
        lambda url, **kw: {
            "as_of": "2026-07-01",
            "results": [{"domain": "vapely.ca", "found": False, "open_page_rank": None,
                         "rank": None, "referring_domains": None, "history": []}],
        },
    )
    out = opr.refresh_site_authority(conn)
    assert out["found"] is False
    assert out["authority"] is None
    assert out["history"] == []
    row = conn.execute("SELECT found, authority_score FROM site_authority").fetchone()
    assert row["found"] == 0
    assert row["authority_score"] is None
    conn.close()


def test_refresh_site_authority_persists_and_reloads(monkeypatch, tmp_path):
    conn = _site_conn(
        tmp_path, "site2.sqlite3",
        store_custom_domain="d.ca",
        open_page_rank_api_key="opr_live_x",
    )
    monkeypatch.setattr(
        opr,
        "fetch_site_authority",
        lambda key, dom: {
            "domain": "d.ca", "found": True, "authority": 4.2, "rank": 130,
            "referring_domains": 96, "as_of": "2026-07-01",
            "history": [
                {"date": "2026-06-01", "authority": 4.1, "estimated": False},
                {"date": "2026-07-01", "authority": 4.2, "estimated": False},
            ],
        },
    )
    opr.refresh_site_authority(conn)
    reloaded = opr.load_site_authority(conn)
    assert reloaded["found"] is True
    assert reloaded["authority"] == 4.2
    assert len(reloaded["history"]) == 2

    # Re-running must upsert history rather than duplicate it.
    opr.refresh_site_authority(conn)
    assert len(opr.load_site_authority(conn)["history"]) == 2
    conn.close()


def test_competitor_authority_benchmark_ignores_unscored(tmp_path):
    conn = _site_conn(tmp_path, "bench.sqlite3")
    conn.executemany(
        "INSERT INTO competitor_profiles (domain, authority_score) VALUES (?, ?)",
        [("a.ca", 2.0), ("b.ca", 6.0), ("c.ca", None)],
    )
    conn.commit()
    b = opr.competitor_authority_benchmark(conn)
    assert b["scored_competitors"] == 2
    assert b["avg_authority"] == 4.0
    assert b["max_authority"] == 6.0
    assert b["top_domain"] == "b.ca"
    conn.close()
