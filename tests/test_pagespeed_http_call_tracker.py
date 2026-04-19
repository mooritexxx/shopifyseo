"""Rolling window counter for PageSpeed HTTP calls (sync status UI)."""

import time

from shopifyseo.dashboard_actions._state import (
    PAGESPEED_HTTP_TRACK_WINDOW_SECONDS,
    SYNC_STATE,
    clear_pagespeed_http_call_tracker,
    record_pagespeed_http_api_call,
    refresh_pagespeed_http_calls_window,
)


def test_pagespeed_http_call_tracker_counts_and_expires(monkeypatch):
    clear_pagespeed_http_call_tracker()
    t0 = 1_000_000.0
    monkeypatch.setattr(time, "monotonic", lambda: t0)
    record_pagespeed_http_api_call()
    assert SYNC_STATE["pagespeed_http_calls_last_60s"] == 1
    monkeypatch.setattr(time, "monotonic", lambda: t0 + 1.0)
    record_pagespeed_http_api_call()
    assert SYNC_STATE["pagespeed_http_calls_last_60s"] == 2
    monkeypatch.setattr(time, "monotonic", lambda: t0 + PAGESPEED_HTTP_TRACK_WINDOW_SECONDS + 2.0)
    refresh_pagespeed_http_calls_window()
    assert SYNC_STATE["pagespeed_http_calls_last_60s"] == 0
