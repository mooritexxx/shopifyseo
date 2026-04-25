"""Tests for SerpAPI audience question enrichment."""

import json

import pytest

import shopifyseo.audience_questions_api as aq
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def conn():
    import sqlite3

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_dashboard_schema(c)
    return c


def test_related_searches_from_payload_position_and_query():
    data = {
        "related_searches": [
            {"query": "  ", "position": 1},
            {"query": "second query", "position": 3},
            "plain string chip",
            {"query": "no explicit position"},
        ]
    }
    out = aq._related_searches_from_payload(data)
    assert out == [
        {"query": "second query", "position": 3},
        {"query": "plain string chip", "position": 3},
        {"query": "no explicit position", "position": 4},
    ]


def test_top_organic_pages_from_payload():
    data = {
        "organic_results": [
            {"title": "Example Guide", "link": "https://example.com/a"},
            {"title": "", "link": "https://bare.url/b"},
            {"title": "No link"},
        ]
    }
    assert aq._top_organic_pages_from_payload(data) == [
        {"title": "Example Guide", "url": "https://example.com/a"},
        {"title": "https://bare.url/b", "url": "https://bare.url/b"},
    ]


def test_qa_from_related_payload_uses_snippet_only():
    data = {
        "related_questions": [
            {"question": "Is ZYN legal?", "snippet": "Yes in some provinces.", "title": "Ignore title"},
            {"question": "  "},
            "Plain string?",
            {
                "question": "Has extras?",
                "snippet": "Short.",
                "text_blocks": [{"snippet": "Ignored block."}],
            },
        ]
    }
    assert aq._qa_from_related_payload(data) == [
        {"question": "Is ZYN legal?", "snippet": "Yes in some provinces."},
        {"question": "Plain string?", "snippet": ""},
        {"question": "Has extras?", "snippet": "Short."},
    ]


def test_enrich_without_serpapi_key_empty(conn):
    ideas = [{"primary_keyword": "vape canada"}, {"primary_keyword": ""}]
    aq.enrich_article_ideas_with_audience_questions(conn, ideas)
    assert ideas[0]["audience_questions"] == []
    assert ideas[0]["top_ranking_pages"] == []
    assert ideas[0]["ai_overview"] is None
    assert ideas[0]["related_searches"] == []
    assert ideas[0]["paa_expansion"] == []
    assert ideas[1]["audience_questions"] == []
    assert ideas[1]["top_ranking_pages"] == []
    assert ideas[1]["ai_overview"] is None
    assert ideas[1]["related_searches"] == []
    assert ideas[1]["paa_expansion"] == []


def test_ai_overview_from_payload():
    data = {
        "ai_overview": {
            "text_blocks": [
                {"type": "paragraph", "snippet": "Hello overview.", "reference_indexes": [0, 1]},
                {"type": "list", "list": [{"snippet": "Item A"}, {"snippet": "B", "snippet_latex": ["x^2"]}]},
                {"type": "ignore_other"},
            ],
            "references": [
                {"title": "T0", "link": "https://a.example", "snippet": "S0", "source": "A", "index": 0},
                {"title": "Skip", "snippet": "no link", "index": 9},
            ],
        }
    }
    out = aq._ai_overview_from_payload(data)
    assert out is not None
    assert [b["type"] for b in out["text_blocks"]] == ["paragraph", "list"]
    assert out["text_blocks"][0]["snippet"] == "Hello overview."
    assert out["text_blocks"][0]["reference_indexes"] == [0, 1]
    assert len(out["text_blocks"][1]["list"]) == 2
    assert out["text_blocks"][1]["list"][1]["snippet_latex"] == ["x^2"]
    assert len(out["references"]) == 1
    assert out["references"][0]["link"] == "https://a.example"


def test_run_serpapi_connection_test_no_key(conn):
    out = aq.run_serpapi_connection_test(conn, api_key_override="")
    assert out["ok"] is False
    assert "empty" in (out["detail"] or "").lower() or "key" in (out["detail"] or "").lower()


def test_enrich_with_serpapi_mock(monkeypatch: pytest.MonkeyPatch, conn):
    from shopifyseo import dashboard_google as dg

    def fake_get_setting(_c: object, key: str) -> str:
        return "test-key-123" if key == "serpapi_api_key" else ""

    monkeypatch.setattr(dg, "get_service_setting", fake_get_setting)

    class FakeResp:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "related_questions": [
                        {"question": "First?", "snippet": "Snip one.", "title": "Site"},
                        {"question": "Second?", "snippet": ""},
                    ],
                    "organic_results": [
                        {"title": "Rank one", "link": "https://example.com/1"},
                        {"title": "Rank two", "url": "https://example.org/two"},
                    ],
                    "ai_overview": {
                        "text_blocks": [{"type": "paragraph", "snippet": "AI says hi."}],
                        "references": [
                            {"title": "Ref", "link": "https://ref.example/r", "snippet": "", "source": "R", "index": 0},
                        ],
                    },
                    "related_searches": [
                        {"query": "zyn nicotine pouches", "position": 1},
                        {"query": "zyn flavors", "position": 2},
                    ],
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(aq, "urlopen", lambda *a, **k: FakeResp())

    ideas = [{"primary_keyword": "zyn canada"}]
    aq.enrich_article_ideas_with_audience_questions(conn, ideas)
    assert ideas[0]["audience_questions"] == [
        {"question": "First?", "snippet": "Snip one."},
        {"question": "Second?", "snippet": ""},
    ]
    assert ideas[0]["top_ranking_pages"] == [
        {"title": "Rank one", "url": "https://example.com/1"},
        {"title": "Rank two", "url": "https://example.org/two"},
    ]
    aio = ideas[0]["ai_overview"]
    assert aio is not None
    assert aio["text_blocks"][0]["snippet"] == "AI says hi."
    assert aio["references"][0]["link"] == "https://ref.example/r"
    assert ideas[0]["related_searches"] == [
        {"query": "zyn nicotine pouches", "position": 1},
        {"query": "zyn flavors", "position": 2},
    ]
    assert ideas[0]["paa_expansion"] == []


def test_expand_paa_fetches_google_related_questions(monkeypatch: pytest.MonkeyPatch, conn):
    from shopifyseo import dashboard_google as dg

    def fake_get_setting(_c: object, key: str) -> str:
        return "k" if key == "serpapi_api_key" else ""

    monkeypatch.setattr(dg, "get_service_setting", fake_get_setting)

    _call = 0

    def fake_urlopen(_req: object, *a, **k):
        nonlocal _call
        _call += 1

        class Resp:
            def read(self) -> bytes:
                if _call == 1:
                    return json.dumps(
                        {
                            "related_questions": [
                                {
                                    "question": "Parent Q?",
                                    "snippet": "P snip.",
                                    "next_page_token": "tok-abc-123",
                                }
                            ],
                        }
                    ).encode()
                return json.dumps(
                    {
                        "related_questions": [
                            {"question": "Child A?", "snippet": "A ans."},
                            {"question": "Child B?", "snippet": "B ans."},
                        ]
                    }
                ).encode()

            def __enter__(self) -> "Resp":
                return self

            def __exit__(self, *x: object) -> None:
                return None

        return Resp()

    monkeypatch.setattr(aq, "urlopen", fake_urlopen)
    out = aq.fetch_serpapi_primary_keyword_snapshot(conn, "test query", expand_paa=True)
    assert out["paa_expansion"] == [
        {
            "parent_question": "Parent Q?",
            "children": [
                {"question": "Child A?", "snippet": "A ans."},
                {"question": "Child B?", "snippet": "B ans."},
            ],
        }
    ]
    assert _call == 2
