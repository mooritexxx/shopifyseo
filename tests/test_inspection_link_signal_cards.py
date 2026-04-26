import sqlite3

from backend.app.services._catalog_helpers import _signal_cards_for


def _current() -> dict:
    return {
        "handle": "sample-product",
        "url": "https://example.com/products/sample-product",
        "index_status": "",
        "index_coverage": "",
        "google_canonical": "",
        "index_last_fetched_at": None,
        "gsc_clicks": 0,
        "gsc_impressions": 0,
        "gsc_ctr": 0.0,
        "gsc_position": 0.0,
        "gsc_last_fetched_at": None,
        "ga4_sessions": 0,
        "ga4_views": 0,
        "ga4_avg_session_duration": 0.0,
        "ga4_last_fetched_at": None,
        "pagespeed_performance": None,
        "pagespeed_seo": None,
        "pagespeed_status": "",
        "pagespeed_last_fetched_at": None,
        "pagespeed_desktop_performance": None,
        "pagespeed_desktop_seo": None,
        "pagespeed_desktop_status": "",
        "pagespeed_desktop_last_fetched_at": None,
    }


def _signals(inspection_detail: dict | None) -> dict:
    return {
        "site_url": "sc-domain:example.com",
        "inspection_detail": inspection_detail,
        "gsc_detail": None,
        "ga4_summary": None,
        "pagespeed_detail": None,
        "errors": {},
    }


def test_index_card_omits_generic_search_console_href_when_no_inspection_deep_link():
    conn = sqlite3.connect(":memory:")
    cards = _signal_cards_for(
        conn,
        "product",
        _current(),
        signals=_signals({"inspectionResult": {"indexStatusResult": {}}}),
    )

    index_card = next(card for card in cards if card["step"] == "index")
    assert index_card["action_label"] == "Request indexing"
    assert index_card["action_href"] is None


def test_index_card_keeps_cached_inspection_deep_link():
    conn = sqlite3.connect(":memory:")
    deep_link = "https://search.google.com/search-console/inspect?resource_id=x&id=y"
    cards = _signal_cards_for(
        conn,
        "product",
        _current(),
        signals=_signals({"inspectionResult": {"inspectionResultLink": deep_link}}),
    )

    index_card = next(card for card in cards if card["step"] == "index")
    assert index_card["action_href"] == deep_link
