"""Tests for WEAVE_SCHEMA wrapping and provider schema normalization.

All tests mock `shopifyseo.dashboard_ai_engine_parts.providers.request_json`
so no network calls are made.
"""
import json
from unittest.mock import Mock, patch

import pytest

from internal_links_support import BASE, OLD, Shopify, database
from shopifyseo.dashboard_ai_engine_parts.prompts import single_field_response_schema
from shopifyseo.dashboard_ai_engine_parts.providers import (
    _call_anthropic,
    _call_gemini,
    _call_ollama,
    _call_openai,
    _call_openrouter,
    _openai_style_json_schema,
)
from shopifyseo.internal_links.ai_weave import WEAVE_SCHEMA, generate_ai_anchor
from shopifyseo.internal_links.safety import LinkConflict


class TestOpenaiStyleJsonSchemaHelper:
    """Unit tests for the _openai_style_json_schema helper."""

    def test_already_wrapped_schema_unchanged(self):
        wrapped = {
            "name": "test_schema",
            "strict": True,
            "schema": {"type": "object", "properties": {"foo": {"type": "string"}}},
        }
        result = _openai_style_json_schema(wrapped, stage="test")
        assert result == wrapped

    def test_bare_schema_gets_wrapped(self):
        bare = {"type": "object", "properties": {"bar": {"type": "string"}}, "additionalProperties": False}
        original = dict(bare)
        result = _openai_style_json_schema(bare, stage="link weave/test")
        assert result["name"] == "link_weave_test"
        assert result["schema"] == bare
        assert "strict" not in result
        assert bare == original

    def test_bare_schema_empty_stage(self):
        bare = {"type": "object", "properties": {"x": {"type": "number"}}}
        result = _openai_style_json_schema(bare, stage="")
        assert result["name"] == "response"
        assert result["schema"] == bare

    def test_weave_schema_is_already_wrapped(self):
        result = _openai_style_json_schema(WEAVE_SCHEMA, stage="link_weave")
        assert result is WEAVE_SCHEMA


class TestOpenRouterWithWeaveSchema:
    """_call_openrouter with WEAVE_SCHEMA sends the wrapped format."""

    def test_response_format_structure(self):
        captured = {}

        def mock_request_json(url, *, method, headers, payload, timeout):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": '{"anchor_phrase": "test"}'}}]}

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", mock_request_json):
            _call_openrouter("test-key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=WEAVE_SCHEMA, stage="link_weave")

        rf = captured["payload"]["response_format"]
        assert rf["type"] == "json_schema"
        js = rf["json_schema"]
        assert js["name"] == "link_weave_edit"
        assert js["strict"] is True
        assert js["schema"]["additionalProperties"] is False
        assert set(js["schema"]["required"]) == {"anchor_phrase", "insert_sentence", "insert_after_text"}


class TestOpenAIWithWeaveSchema:
    """_call_openai with WEAVE_SCHEMA sends the wrapped format."""

    def test_response_format_structure(self):
        captured = {}

        def mock_request_json(url, *, method, headers, payload, timeout):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": '{"anchor_phrase": "test"}'}}]}

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", mock_request_json):
            _call_openai("test-key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=WEAVE_SCHEMA, stage="link_weave")

        rf = captured["payload"]["response_format"]
        assert rf["type"] == "json_schema"
        js = rf["json_schema"]
        assert js["name"] == "link_weave_edit"
        assert js["strict"] is True
        assert js["schema"]["additionalProperties"] is False
        assert set(js["schema"]["required"]) == {"anchor_phrase", "insert_sentence", "insert_after_text"}


class TestBareSchemaWrapping:
    """Bare schemas get wrapped without strict for both OpenAI and OpenRouter."""

    @pytest.mark.parametrize("call_fn,url_pattern", [
        (_call_openai, "openai"),
        (_call_openrouter, "openrouter"),
    ])
    def test_bare_schema_wrapped_without_strict(self, call_fn, url_pattern):
        bare = {"type": "object", "properties": {"x": {"type": "string"}}, "additionalProperties": False}
        original = dict(bare)
        captured = {}

        def mock_request_json(url, *, method, headers, payload, timeout):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": '{"x": "val"}'}}]}

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", mock_request_json):
            call_fn("test-key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=bare, stage="link weave/test")

        rf = captured["payload"]["response_format"]
        js = rf["json_schema"]
        assert js["name"] == "link_weave_test"
        assert js["schema"] == original
        assert "strict" not in js
        assert bare == original


class TestAlreadyWrappedSchemaPassthrough:
    """Already-wrapped schemas (like from prompts.single_field_response_schema) pass unchanged."""

    @pytest.mark.parametrize("call_fn", [_call_openai, _call_openrouter])
    def test_wrapped_schema_unchanged(self, call_fn):
        wrapped = single_field_response_schema("product", "seo_title")
        captured = {}

        def mock_request_json(url, *, method, headers, payload, timeout):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": '{"seo_title": "Test Title"}'}}]}

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", mock_request_json):
            call_fn("test-key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=wrapped, stage="regen")

        rf = captured["payload"]["response_format"]
        js = rf["json_schema"]
        assert js == wrapped


class TestOtherProvidersSamePayload:
    """Gemini, Anthropic, and Ollama produce the same provider payload for wrapped and bare schemas."""

    def test_gemini_same_response_schema(self):
        wrapped = {
            "name": "test",
            "strict": True,
            "schema": {"type": "object", "properties": {"foo": {"type": "string"}}, "required": ["foo"]},
        }
        bare = wrapped["schema"]
        captured_wrapped = {}
        captured_bare = {}

        def make_mock(captured):
            def mock_fn(url, *, method, headers, payload, timeout):
                captured["payload"] = payload
                return {"candidates": [{"content": {"parts": [{"text": '{"foo": "bar"}'}]}}]}
            return mock_fn

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", make_mock(captured_wrapped)):
            _call_gemini("key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=wrapped)
        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", make_mock(captured_bare)):
            _call_gemini("key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=bare)

        assert captured_wrapped["payload"]["generationConfig"]["responseSchema"] == captured_bare["payload"]["generationConfig"]["responseSchema"]

    def test_anthropic_same_input_schema(self):
        wrapped = {
            "name": "test",
            "strict": True,
            "schema": {"type": "object", "properties": {"foo": {"type": "string"}}, "required": ["foo"]},
        }
        bare = wrapped["schema"]
        captured_wrapped = {}
        captured_bare = {}

        def make_mock(captured):
            def mock_fn(url, *, method, headers, payload, timeout):
                captured["payload"] = payload
                return {"content": [{"type": "tool_use", "input": {"foo": "bar"}}]}
            return mock_fn

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", make_mock(captured_wrapped)):
            _call_anthropic("key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=wrapped)
        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", make_mock(captured_bare)):
            _call_anthropic("key", "model", [{"role": "user", "content": "hi"}], 60, json_schema=bare)

        assert captured_wrapped["payload"]["tools"][0]["input_schema"] == captured_bare["payload"]["tools"][0]["input_schema"]

    def test_ollama_same_format(self):
        wrapped = {
            "name": "test",
            "strict": True,
            "schema": {"type": "object", "properties": {"foo": {"type": "string"}}, "required": ["foo"]},
        }
        bare = wrapped["schema"]
        captured_wrapped = {}
        captured_bare = {}

        def make_mock(captured):
            def mock_fn(url, *, method, headers, payload, timeout):
                captured["payload"] = payload
                return {"message": {"content": '{"foo": "bar"}'}}
            return mock_fn

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", make_mock(captured_wrapped)):
            _call_ollama("http://localhost:11434", "", "model", [{"role": "user", "content": "hi"}], 60, json_schema=wrapped)
        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", make_mock(captured_bare)):
            _call_ollama("http://localhost:11434", "", "model", [{"role": "user", "content": "hi"}], 60, json_schema=bare)

        assert captured_wrapped["payload"]["format"] == captured_bare["payload"]["format"]


class TestGenerateAiAnchorDefaultPath:
    """Regression tests through generate_ai_anchor without injecting call_ai_fn."""

    def test_accepted_reply_with_empty_fields(self, db_conn):
        """Accepted reply with fence and empty insert_sentence/insert_after_text."""
        conn = database(db_conn)
        live = Shopify(OLD + '<p>New live text.</p>')
        conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
        conn.commit()
        captured = {}

        reply = '```json\n{"anchor_phrase": "ceramic tanks", "insert_sentence": "", "insert_after_text": ""}\n```'

        def mock_request_json(url, *, method, headers, payload, timeout):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": reply}}]}

        def mock_settings(conn):
            return {
                "generation_provider": "openrouter",
                "generation_model": "google/gemini-3.5-flash-lite",
                "openrouter_api_key": "test-key",
            }

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", mock_request_json), \
             patch("shopifyseo.dashboard_store.db_connect", return_value=Mock(close=Mock())), \
             patch("shopifyseo.dashboard_ai_engine_parts.settings.ai_settings", mock_settings):

            result = generate_ai_anchor(conn, 1, BASE, fetch_fn=live.fetch)

        assert result["edit"] == {"anchor_phrase": "ceramic tanks"}
        row = conn.execute("SELECT ai_edit_json FROM link_suggestions").fetchone()
        assert json.loads(row["ai_edit_json"]) == {"anchor_phrase": "ceramic tanks"}

        rf = captured["payload"]["response_format"]
        assert rf["json_schema"]["name"] == "link_weave_edit"
        assert "schema" in rf["json_schema"]

        live.push.assert_not_called()
        db_body = conn.execute("SELECT description_html FROM products WHERE handle='source'").fetchone()[0]
        assert db_body == OLD

    def test_extra_url_key_still_blocked(self, db_conn):
        """Probe reply with extra `url` key is still rejected."""
        conn = database(db_conn)
        live = Shopify(OLD)
        conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
        conn.commit()

        probe_reply = (
            '```json\n'
            '{\n'
            '  "anchor_phrase": "Envi Disposable Vapes",\n'
            '  "url": "https://vapely.ca/collections/envi-disposable-vapes",\n'
            '  "insert_sentence": "To view all available options, be sure to browse our full selection of Envi Disposable Vapes.",\n'
            '  "insert_after_text": "Vapely offers an extensive selection of top-tier hardware tailored to adult nicotine consumers in Canada."\n'
            '}\n'
            '```'
        )

        def mock_request_json(url, *, method, headers, payload, timeout):
            return {"choices": [{"message": {"content": probe_reply}}]}

        def mock_settings(conn):
            return {
                "generation_provider": "openrouter",
                "generation_model": "google/gemini-3.5-flash-lite",
                "openrouter_api_key": "test-key",
            }

        with patch("shopifyseo.dashboard_ai_engine_parts.providers.request_json", mock_request_json), \
             patch("shopifyseo.dashboard_store.db_connect", return_value=Mock(close=Mock())), \
             patch("shopifyseo.dashboard_ai_engine_parts.settings.ai_settings", mock_settings):

            with pytest.raises(LinkConflict, match="unsupported edit fields"):
                generate_ai_anchor(conn, 1, BASE, fetch_fn=live.fetch)

        row = conn.execute("SELECT ai_edit_json FROM link_suggestions").fetchone()
        assert row["ai_edit_json"] is None

        live.push.assert_not_called()
        db_body = conn.execute("SELECT description_html FROM products WHERE handle='source'").fetchone()[0]
        assert db_body == OLD
