import threading
import time

from shopifyseo.dashboard_actions import _ai


class DummyConnection:
    def close(self) -> None:
        pass


def test_refresh_and_get_inspection_link_uses_cached_deep_link(monkeypatch):
    calls: list[bool] = []
    refreshed: list[tuple[str, str]] = []

    def fake_get_url_inspection(conn, url, *, refresh, object_type, object_handle):
        calls.append(refresh)
        return {
            "inspectionResult": {
                "inspectionResultLink": "https://search.google.com/search-console/inspect?id=cached"
            }
        }

    monkeypatch.setattr(
        _ai.dq, "object_url", lambda kind, handle: f"https://example.com/{handle}"
    )
    monkeypatch.setattr(_ai.dg, "get_url_inspection", fake_get_url_inspection)
    monkeypatch.setattr(
        _ai,
        "refresh_object_structured_seo_data",
        lambda conn, kind, handle: refreshed.append((kind, handle)),
    )

    link = _ai.refresh_and_get_inspection_link(
        lambda: DummyConnection(), "product", "sample-product"
    )

    assert link == "https://search.google.com/search-console/inspect?id=cached"
    assert calls == [False]
    assert refreshed == []


def test_refresh_and_get_inspection_link_serializes_fresh_refreshes(monkeypatch):
    active_refreshes = 0
    max_active_refreshes = 0
    active_lock = threading.Lock()
    start_barrier = threading.Barrier(2)

    def fake_get_url_inspection(conn, url, *, refresh, object_type, object_handle):
        nonlocal active_refreshes, max_active_refreshes
        if not refresh:
            return {"inspectionResult": {}}

        with active_lock:
            active_refreshes += 1
            max_active_refreshes = max(max_active_refreshes, active_refreshes)
        time.sleep(0.05)
        with active_lock:
            active_refreshes -= 1
        return {
            "inspectionResult": {
                "inspectionResultLink": f"https://search.google.com/search-console/inspect?id={object_handle}"
            }
        }

    monkeypatch.setattr(
        _ai.dq, "object_url", lambda kind, handle: f"https://example.com/{handle}"
    )
    monkeypatch.setattr(_ai.dg, "get_url_inspection", fake_get_url_inspection)
    monkeypatch.setattr(
        _ai, "refresh_object_structured_seo_data", lambda conn, kind, handle: None
    )

    results: list[str] = []

    def run_refresh(handle: str) -> None:
        start_barrier.wait(timeout=2)
        results.append(
            _ai.refresh_and_get_inspection_link(lambda: DummyConnection(), "product", handle)
        )

    threads = [
        threading.Thread(target=run_refresh, args=(handle,))
        for handle in ("first-product", "second-product")
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(results) == [
        "https://search.google.com/search-console/inspect?id=first-product",
        "https://search.google.com/search-console/inspect?id=second-product",
    ]
    assert max_active_refreshes == 1
