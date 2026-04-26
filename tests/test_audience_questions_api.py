"""Tests for SerpAPI audience question enrichment."""

import json
from urllib.parse import parse_qs, urlparse

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


def test_serpapi_us_fallback_after_ca_empty_error(monkeypatch: pytest.MonkeyPatch):
    """Short queries may return the organic-empty error on regional Google but succeed on google.com."""
    _call = 0

    def fake_one(_key: str, _kw: str, loc: object) -> object:
        nonlocal _call
        _call += 1
        if _call == 1:
            assert isinstance(loc, dict) and (loc.get("gl") or "").lower() == "ca"
            return (
                [],
                [],
                None,
                [],
                "Google hasn't returned any results for this query.",
                None,
            )
        assert _call == 2
        assert isinstance(loc, dict) and (loc.get("gl") or "").lower() == "us"
        return (
            [{"question": "Q?", "snippet": ""}],
            [{"title": "R", "url": "https://u"}],
            None,
            [],
            None,
            {"related_questions": []},
        )

    monkeypatch.setattr("shopifyseo.audience_questions_api._serpapi_one_google_organic_request", fake_one)
    r = aq._serpapi_fetch_google_serp_snapshot("k", "pod rate", localization={"gl": "ca", "hl": "en", "google_domain": "google.ca"})
    assert r[4] is None
    assert len(r[0]) == 1
    assert r[6] == {"gl": "us", "hl": "en", "google_domain": "google.com"}
    assert _call == 2


def test_soft_serpapi_error_ignored_when_paa_present(conn, monkeypatch: pytest.MonkeyPatch):
    """SerpAPI may set an organic-empty error while related_questions is still populated."""
    from shopifyseo import dashboard_google as dg

    def fake_get_setting(_c: object, key: str) -> str:
        return "test-key" if key == "serpapi_api_key" else ""

    monkeypatch.setattr(dg, "get_service_setting", fake_get_setting)

    class FakeResp:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "error": "Google hasn't returned any results for this query.",
                    "related_questions": [
                        {"question": "What is a pod in a vape?", "snippet": "A pod is…"},
                    ],
                    "organic_results": [],
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(aq, "urlopen", lambda *a, **k: FakeResp())
    out = aq.fetch_serpapi_primary_keyword_snapshot(conn, "pod vape", expand_paa=False)
    assert out.get("serpapi_error") is None
    assert out["audience_questions"] == [
        {"question": "What is a pod in a vape?", "snippet": "A pod is…"},
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
    # One HTTP per parent is enough; avoid an extra "same token" follow-up in this test.
    monkeypatch.setenv("PAA_SAME_TOKEN_EXTRA_ROUNDS", "0")

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


def test_refresh_snapshot_uses_informational_paa_fallback_without_replacing_exact_serp(
    monkeypatch: pytest.MonkeyPatch,
    conn,
):
    from shopifyseo import dashboard_google as dg

    def fake_get_setting(_c: object, key: str) -> str:
        return "k" if key == "serpapi_api_key" else ""

    monkeypatch.setattr(dg, "get_service_setting", fake_get_setting)
    monkeypatch.setenv("PAA_FALLBACK_DELAY_SEC", "0")
    monkeypatch.setenv("PAA_SAME_TOKEN_EXTRA_ROUNDS", "0")
    seen_queries: list[str] = []

    def fake_urlopen(req: object, *a, **k):
        url = getattr(req, "full_url", "")
        params = parse_qs(urlparse(url).query)
        engine = params.get("engine", [""])[0]
        query = params.get("q", [""])[0]
        seen_queries.append(query or engine)

        class Resp:
            def read(self) -> bytes:
                if engine == "google_related_questions":
                    return json.dumps(
                        {
                            "related_questions": [
                                {"question": "Which accessories matter?", "snippet": "Coils, pods, chargers."}
                            ]
                        }
                    ).encode()
                if query == "vape accessories canada":
                    return json.dumps(
                        {
                            "organic_results": [
                                {"title": "Exact Rank", "link": "https://exact.example/rank"}
                            ],
                            "related_searches": [{"query": "vape accessories online", "position": 1}],
                        }
                    ).encode()
                assert query == "what are vape accessories"
                return json.dumps(
                    {
                        "related_questions": [
                            {
                                "question": "What accessories do you need for a vape?",
                                "snippet": "Common basics include coils and chargers.",
                                "next_page_token": "paa-token",
                            }
                        ],
                        "organic_results": [
                            {"title": "Fallback Rank", "link": "https://fallback.example/rank"}
                        ],
                    }
                ).encode()

            def __enter__(self) -> "Resp":
                return self

            def __exit__(self, *x: object) -> None:
                return None

        return Resp()

    monkeypatch.setattr(aq, "urlopen", fake_urlopen)

    out = aq.fetch_serpapi_primary_keyword_snapshot(
        conn,
        "vape accessories canada",
        expand_paa=True,
    )

    assert out["top_ranking_pages"] == [
        {"title": "Exact Rank", "url": "https://exact.example/rank"}
    ]
    assert out["related_searches"] == [{"query": "vape accessories online", "position": 1}]
    assert out["audience_questions"] == [
        {
            "question": "What accessories do you need for a vape?",
            "snippet": "Common basics include coils and chargers.",
        }
    ]
    assert out["paa_expansion"] == [
        {
            "parent_question": "What accessories do you need for a vape?",
            "children": [
                {"question": "Which accessories matter?", "snippet": "Coils, pods, chargers."}
            ],
        }
    ]
    assert seen_queries[:3] == [
        "vape accessories canada",
        "what are vape accessories",
        "google_related_questions",
    ]


def test_paa_children_pagination_uses_continuation_token(monkeypatch: pytest.MonkeyPatch):
    """Second request uses last item's next_page_token when it differs from the request token."""
    calls: list[str] = []

    def fake_fetch(_key: str, token: str, _loc: object) -> dict[str, object] | None:
        calls.append(str(token).strip())
        if len(calls) == 1:
            return {
                "related_questions": [
                    {
                        "question": "One?",
                        "snippet": "a",
                        "next_page_token": "T-step-2",
                    },
                ],
            }
        if len(calls) == 2 and calls[-1] == "T-step-2":
            return {
                "related_questions": [
                    {"question": "Two?", "snippet": "b"},
                ],
            }
        return None

    monkeypatch.setattr(
        "shopifyseo.audience_questions_api._fetch_google_related_questions_expansion", fake_fetch
    )
    monkeypatch.setenv("PAA_SAME_TOKEN_EXTRA_ROUNDS", "0")
    out = aq._collect_paa_children_for_one_parent(
        "k", "T-step-1", "Parent Q?", {}, 0, 10
    )
    assert [x["question"] for x in out] == ["One?", "Two?"]
    assert calls == ["T-step-1", "T-step-2"]


def test_expand_paa_uses_question_search_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows without next_page_token / serpapi_link use Google search for the question (fallback)."""
    def fake_q(_api: str, q: str, _loc: object, _mx: int) -> list[dict[str, str]]:
        assert "orphan" in q.lower()
        return [{"question": "From fallback?", "snippet": "x"}]

    monkeypatch.setattr(
        "shopifyseo.audience_questions_api._paa_children_from_google_question_search", fake_q
    )
    out = aq.expand_paa_via_related_questions_engine(
        "k",
        {"related_questions": [{"question": "Orphan parent?", "snippet": "a"}]},
        {"gl": "us", "hl": "en", "google_domain": "google.com"},
    )
    assert out == [
        {
            "parent_question": "Orphan parent?",
            "children": [{"question": "From fallback?", "snippet": "x"}],
        }
    ]


def test_paa_continuation_uses_serpapi_link_when_json_token_matches_request() -> None:
    """If only ``serpapi_link`` encodes a different next token, we still continue."""
    p = {
        "related_questions": [
            {
                "question": "A?",
                "next_page_token": "REQ-TOK",
            },
            {
                "question": "B?",
                "next_page_token": "REQ-TOK",
                "serpapi_link": "https://serpapi.com/search.json?engine=google_related_questions&next_page_token=OTHER-TOK-XYZ"
                "&google_domain=google.com",
            },
        ],
    }
    n = aq._paa_continuation_token_from_expansion(p, "REQ-TOK")
    assert n == "OTHER-TOK-XYZ"
