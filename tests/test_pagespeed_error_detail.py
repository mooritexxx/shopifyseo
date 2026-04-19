"""PageSpeed sync error detail formatting for UI / status."""

from shopifyseo.dashboard_actions._sync import _pagespeed_error_detail_for_ui
from shopifyseo.dashboard_http import HttpRequestError


def test_pagespeed_error_detail_extracts_google_error_message():
    body = (
        '{"error":{"code":429,"message":"Quota exceeded for quota metric queries",'
        '"status":"RESOURCE_EXHAUSTED"}}'
    )
    exc = HttpRequestError("HTTP 429 for https://pagespeedonline.googleapis.com/x", status=429, body=body)
    msg, extra = _pagespeed_error_detail_for_ui(exc)
    assert "HTTP 429" in msg
    assert "Quota exceeded" in msg
    assert "RESOURCE_EXHAUSTED" in msg
    assert extra["http_status"] == 429
    assert "Quota exceeded" in extra["response_body"]


def test_pagespeed_error_detail_non_json_body_appends_snippet():
    exc = HttpRequestError(
        "HTTP 500 for https://pagespeedonline.googleapis.com/y",
        status=500,
        body="<html><title>Error</title></html>",
    )
    msg, extra = _pagespeed_error_detail_for_ui(exc)
    assert "HTTP 500" in msg
    assert "<html>" in msg
    assert extra["http_status"] == 500
    assert "<html>" in extra["response_body"]
