"""Open PageRank client — domain authority for competitor profiles."""

import sqlite3

import pytest

from backend.app.services import open_page_rank as opr
from shopifyseo.dashboard_store import ensure_dashboard_schema


def _fake_response(rows):
    return {"status_code": 200, "response": rows}


def test_fetch_domain_authority_parses_scores(monkeypatch):
    captured = {}

    def fake_request_json(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _fake_response(
            [
                {"domain": "a.com", "status_code": 200, "page_rank_decimal": 5.42, "rank": "1200"},
                {"domain": "b.com", "status_code": 200, "page_rank_decimal": 3, "rank": None},
            ]
        )

    monkeypatch.setattr(opr, "request_json", fake_request_json)
    out = opr.fetch_domain_authority("opr_live_key", ["A.com", "b.com"])

    assert out == {
        "a.com": {"authority": 5.42, "rank": 1200},
        "b.com": {"authority": 3.0, "rank": None},
    }
    assert captured["headers"]["Authorization"] == "Bearer opr_live_key"
    assert "domains%5B%5D=a.com" in captured["url"]


def test_unindexed_domains_are_omitted_not_zero(monkeypatch):
    """A domain Open PageRank does not know is unknown, not authority 0."""

    def fake_request_json(url, **kwargs):
        return _fake_response(
            [
                {"domain": "known.com", "status_code": 200, "page_rank_decimal": 4.1, "rank": "50"},
                {"domain": "unknown.com", "status_code": 404, "page_rank_decimal": "0", "rank": None},
                {"domain": "blank.com", "status_code": 200, "page_rank_decimal": "N/A", "rank": None},
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
        lambda key, domains: {"scored.ca": {"authority": 6.5, "rank": 900}},
    )

    stats = opr.refresh_competitor_authority(conn)
    assert stats == {"checked": 2, "scored": 1, "unknown": 1}

    rows = {
        r["domain"]: r
        for r in conn.execute(
            "SELECT domain, authority_score, authority_rank FROM competitor_profiles"
        )
    }
    assert rows["scored.ca"]["authority_score"] == 6.5
    assert rows["scored.ca"]["authority_rank"] == 900
    # Unknown stays NULL rather than becoming 0.
    assert rows["unscored.ca"]["authority_score"] is None
    assert rows["unscored.ca"]["authority_rank"] is None
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
