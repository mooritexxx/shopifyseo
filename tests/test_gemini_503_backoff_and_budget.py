from shopifyseo.dashboard_ai_engine_parts import providers
from shopifyseo.dashboard_http import HttpRequestError


def test_budget_default_and_env(monkeypatch):
    monkeypatch.delenv("AI_OUTPUT_TOKEN_BUDGET", raising=False)
    assert providers._completion_token_budget_from_json_schema(None, stage="article_draft") == 8192
    monkeypatch.setenv("AI_OUTPUT_TOKEN_BUDGET", "16000")
    assert providers._completion_token_budget_from_json_schema(None, stage="article_draft") == 16000
    monkeypatch.setenv("AI_OUTPUT_TOKEN_BUDGET", "999999")
    assert providers._large_schema_output_token_budget() == 65536
    monkeypatch.setenv("AI_OUTPUT_TOKEN_BUDGET", "junk")
    assert providers._large_schema_output_token_budget() == 8192


def test_gemini_retries_503_then_succeeds(monkeypatch):
    calls = []
    sleeps = []

    def fake(url, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise HttpRequestError("HTTP 503 high demand", status=503)
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

    monkeypatch.setattr(providers, "request_json", fake)
    monkeypatch.setattr(providers, "_sleep_gemini_retry", sleeps.append)
    out = providers._call_gemini("k", "gemini-x", [{"role": "user", "content": "hi"}], 10, json_schema={"schema": {"type": "object"}})
    assert out == {"ok": True}
    assert len(calls) == 3 and sleeps == list(providers.GEMINI_RETRY_BACKOFF_SECONDS[:2])


def test_gemini_does_not_retry_400(monkeypatch):
    calls = []

    def fake(url, **kwargs):
        calls.append(1)
        raise HttpRequestError("HTTP 400", status=400)

    monkeypatch.setattr(providers, "request_json", fake)
    monkeypatch.setattr(providers, "_sleep_gemini_retry", lambda s: None)
    try:
        providers._call_gemini("k", "gemini-x", [{"role": "user", "content": "hi"}], 10)
    except Exception:
        pass
    assert len(calls) == 1
