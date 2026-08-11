"""PageSpeed bulk sync: error/refreshed counts must reflect a job's terminal outcome, not each attempt."""

import sqlite3

from shopifyseo.dashboard_actions import _sync
from shopifyseo.dashboard_actions import _sync_pagespeed as psmod
from shopifyseo.dashboard_google._cache import ensure_google_cache_schema
from shopifyseo.dashboard_http import HttpRequestError


def _prepare_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_google_cache_schema(conn)
    conn.close()


def test_transient_failure_then_success_is_not_counted_as_error(monkeypatch, tmp_path):
    """A job that fails once and succeeds on the final-batch retry must not be tallied as an error."""
    db_path = str(tmp_path / "catalog.sqlite3")
    _prepare_db(db_path)

    url = "https://example.com/products/widget"
    monkeypatch.setattr(_sync, "_all_object_targets", lambda _conn: [("product", "widget", url)])

    attempts = {"mobile": 0, "desktop": 0}

    def _fake_get_pagespeed(_conn, _url, strategy, *, refresh=False, **_kwargs):
        if not refresh:
            # Denormalization read-back after a success (refresh_object_pagespeed_signal_data)
            # — not a real sync attempt.
            return {"_cache": {}}
        attempts[strategy] += 1
        if attempts[strategy] == 1:
            raise HttpRequestError(f"HTTP 500 for {_url}", status=500)
        return {"_cache": {}}

    monkeypatch.setattr(psmod.dg, "get_pagespeed", _fake_get_pagespeed)

    summary = psmod.bulk_refresh_pagespeed(db_path, force_refresh=True)

    assert attempts == {"mobile": 2, "desktop": 2}
    assert summary["refreshed"] == 2
    assert summary["errors"] == 0


def test_permanent_failure_is_counted_exactly_once(monkeypatch, tmp_path):
    """A job that fails on every attempt must be tallied as exactly one error, not once per attempt."""
    db_path = str(tmp_path / "catalog.sqlite3")
    _prepare_db(db_path)

    url = "https://example.com/products/widget"
    monkeypatch.setattr(_sync, "_all_object_targets", lambda _conn: [("product", "widget", url)])

    attempts = {"mobile": 0, "desktop": 0}

    def _fake_get_pagespeed(_conn, _url, strategy, *, refresh=False, **_kwargs):
        if not refresh:
            return {"_cache": {}}
        attempts[strategy] += 1
        raise HttpRequestError(f"HTTP 500 for {_url}", status=500)

    monkeypatch.setattr(psmod.dg, "get_pagespeed", _fake_get_pagespeed)

    summary = psmod.bulk_refresh_pagespeed(db_path, force_refresh=True)

    assert attempts == {"mobile": 2, "desktop": 2}
    assert summary["refreshed"] == 0
    assert summary["errors"] == 2
