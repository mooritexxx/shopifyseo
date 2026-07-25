"""Shopify Admin access-token caching (no live API)."""

import pytest

from shopifyseo import shopify_admin
from shopifyseo.dashboard_http import HttpRequestError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("SHOPIFY_SHOP", "demo.myshopify.com")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "secret")
    shopify_admin.invalidate_shopify_token_cache()
    yield
    shopify_admin.invalidate_shopify_token_cache()


def _stub_token_endpoint(monkeypatch, tokens: list[str], calls: list) -> None:
    def _fake_request_json(url: str, **kwargs):
        calls.append(url)
        return {"access_token": tokens[min(len(calls) - 1, len(tokens) - 1)], "expires_in": 3600}

    monkeypatch.setattr(shopify_admin, "request_json", _fake_request_json)


def test_token_is_fetched_once_and_reused(monkeypatch) -> None:
    calls: list = []
    _stub_token_endpoint(monkeypatch, ["tok-1"], calls)

    assert shopify_admin.token_request() == "tok-1"
    assert shopify_admin.token_request() == "tok-1"
    assert shopify_admin.token_request() == "tok-1"
    assert len(calls) == 1


def test_force_refresh_bypasses_the_cache(monkeypatch) -> None:
    calls: list = []
    _stub_token_endpoint(monkeypatch, ["tok-1", "tok-2"], calls)

    assert shopify_admin.token_request() == "tok-1"
    assert shopify_admin.token_request(force_refresh=True) == "tok-2"
    assert len(calls) == 2


def test_cache_is_keyed_by_shop(monkeypatch) -> None:
    calls: list = []
    _stub_token_endpoint(monkeypatch, ["tok-a", "tok-b"], calls)

    assert shopify_admin.token_request() == "tok-a"
    monkeypatch.setenv("SHOPIFY_SHOP", "other.myshopify.com")
    assert shopify_admin.token_request() == "tok-b"
    assert len(calls) == 2


def test_expired_token_is_refetched(monkeypatch) -> None:
    calls: list = []

    def _fake_request_json(url: str, **kwargs):
        calls.append(url)
        return {"access_token": f"tok-{len(calls)}", "expires_in": 0}

    monkeypatch.setattr(shopify_admin, "request_json", _fake_request_json)

    assert shopify_admin.token_request() == "tok-1"
    assert shopify_admin.token_request() == "tok-2"
    assert len(calls) == 2


def test_graphql_post_retries_once_after_401(monkeypatch) -> None:
    """A revoked cached token must not fail the whole sync."""
    token_calls: list = []
    graphql_calls: list = []

    def _fake_request_json(url: str, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            token_calls.append(url)
            return {"access_token": f"tok-{len(token_calls)}", "expires_in": 3600}
        graphql_calls.append(kwargs.get("headers", {}).get("X-Shopify-Access-Token"))
        if len(graphql_calls) == 1:
            raise HttpRequestError("HTTP 401", status=401, body="unauthorized")
        return {"data": {"ok": True}}

    monkeypatch.setattr(shopify_admin, "request_json", _fake_request_json)

    assert shopify_admin.graphql_post("{ shop { name } }") == {"data": {"ok": True}}
    assert graphql_calls == ["tok-1", "tok-2"]


def test_graphql_post_raises_on_non_401_without_token_refetch(monkeypatch) -> None:
    token_calls: list = []

    def _fake_request_json(url: str, **kwargs):
        if url.endswith("/admin/oauth/access_token"):
            token_calls.append(url)
            return {"access_token": "tok", "expires_in": 3600}
        raise HttpRequestError("HTTP 500", status=500, body="boom")

    monkeypatch.setattr(shopify_admin, "request_json", _fake_request_json)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        shopify_admin.graphql_post("{ shop { name } }")
    assert len(token_calls) == 1
