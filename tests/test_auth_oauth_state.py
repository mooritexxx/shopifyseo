from fastapi.testclient import TestClient

import shopifyseo.dashboard_google as dg
from backend.app.main import app
from backend.app.routers import auth as auth_router

client = TestClient(app)


class DummyConn:
    def close(self):
        return None


def _stub_callback_deps(monkeypatch):
    """Stub DB + Google exchange so the callback never touches the network."""
    exchanges: list[str] = []
    monkeypatch.setattr(auth_router, "open_db_connection", lambda: DummyConn())
    monkeypatch.setattr(auth_router, "apply_runtime_settings", lambda conn: None)
    monkeypatch.setattr(dg, "google_exchange_code", lambda code: exchanges.append(code) or {"access_token": "t"})
    monkeypatch.setattr(dg, "set_service_token", lambda conn, service, payload: None)
    return exchanges


def test_callback_rejects_empty_state_even_when_no_state_is_stored(monkeypatch):
    exchanges = _stub_callback_deps(monkeypatch)
    monkeypatch.setitem(dg.GOOGLE_AUTH_STATE, "value", "")

    response = client.get("/auth/google/callback?code=abc", follow_redirects=False)

    assert response.status_code == 303
    assert "Google+OAuth+state+mismatch" in response.headers["location"]
    assert exchanges == []


def test_callback_consumes_valid_state_exactly_once(monkeypatch):
    exchanges = _stub_callback_deps(monkeypatch)
    monkeypatch.setitem(dg.GOOGLE_AUTH_STATE, "value", "state-token-1")

    first = client.get("/auth/google/callback?state=state-token-1&code=abc", follow_redirects=False)
    assert first.status_code == 303
    assert "Google+Search+Console+connected" in first.headers["location"]
    assert exchanges == ["abc"]

    second = client.get("/auth/google/callback?state=state-token-1&code=abc", follow_redirects=False)
    assert second.status_code == 303
    assert "Google+OAuth+state+mismatch" in second.headers["location"]
    assert exchanges == ["abc"]
