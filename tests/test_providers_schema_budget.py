"""Schema-driven completion token budget (model-agnostic trigger)."""

from shopifyseo.dashboard_ai_engine_parts.providers import (
    _LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET,
    _completion_token_budget_from_json_schema,
    _max_string_min_length_from_response_json_schema,
)


def test_budget_triggers_on_large_nested_minlength():
    schema = {
        "name": "article_draft",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 10},
                "body": {"type": "string", "minLength": 14000},
            },
        },
    }
    assert _max_string_min_length_from_response_json_schema(schema) == 14000
    assert _completion_token_budget_from_json_schema(schema) == _LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET


def test_budget_absent_for_small_schemas():
    schema = {
        "name": "small",
        "schema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "minLength": 50},
            },
        },
    }
    assert _max_string_min_length_from_response_json_schema(schema) == 50
    assert _completion_token_budget_from_json_schema(schema) is None


def test_max_minlength_from_bare_schema():
    bare = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "minLength": 9000},
        },
    }
    assert _max_string_min_length_from_response_json_schema(bare) == 9000
    assert _completion_token_budget_from_json_schema(bare) == _LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET


def test_budget_article_draft_stage_even_when_schema_strings_are_short():
    schema = {
        "name": "article_draft_section_batch",
        "schema": {
            "type": "object",
            "properties": {
                "html_blocks": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "string", "minLength": 400, "maxLength": 8000},
                },
            },
            "required": ["html_blocks"],
        },
    }
    assert _completion_token_budget_from_json_schema(schema, stage="article_draft_section") == _LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET
