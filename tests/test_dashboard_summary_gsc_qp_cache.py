from backend.app.services.dashboard_service import _gsc_query_page_tables_uncached


def _slice(*, exists, stale, error=""):
    return {
        "rows": [],
        "error": error,
        "_cache": {"exists": exists, "stale": stale, "fetched_at": None, "expires_at": None},
    }


def test_fresh_success_is_not_uncached():
    raw = {"queries": _slice(exists=True, stale=False), "pages": _slice(exists=True, stale=False)}
    assert _gsc_query_page_tables_uncached(raw) is False


def test_never_fetched_is_uncached():
    raw = {"queries": _slice(exists=False, stale=True), "pages": _slice(exists=False, stale=True)}
    assert _gsc_query_page_tables_uncached(raw) is True


def test_stale_cached_error_triggers_retry():
    # A transient 429/503 previously got written into the cache as a normal entry;
    # once its TTL has expired, the next request should retry instead of serving
    # the stale error for the rest of the anchor day.
    raw = {
        "queries": _slice(exists=True, stale=True, error="HTTP 429: quotaExceeded"),
        "pages": _slice(exists=True, stale=False),
    }
    assert _gsc_query_page_tables_uncached(raw) is True


def test_fresh_cached_error_does_not_force_retry_every_request():
    # Within the TTL window, a cached error should not force a live Google call
    # on every single request -- only once the cache entry goes stale.
    raw = {
        "queries": _slice(exists=True, stale=False, error="HTTP 429: quotaExceeded"),
        "pages": _slice(exists=True, stale=False),
    }
    assert _gsc_query_page_tables_uncached(raw) is False
