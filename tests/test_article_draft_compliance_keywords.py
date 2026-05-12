"""Tests for required-keyword compliance — both SERP-derived and user-supplied keywords."""
import pytest

from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    validate_article_draft_compliance,
)


def _body_with(*phrases: str, link: str = "") -> str:
    parts = ["<h2>Intro</h2>"]
    if link:
        parts.append(f'<p><a href="{link}">link</a></p>')
    for p in phrases:
        parts.append(f"<p>{p} content goes here with detail.</p>")
    parts.append("<p>" + ("Filler paragraph. " * 950) + "</p>")
    return "".join(parts)


def test_compliance_passes_with_both_keywords_present():
    body = _body_with("best disposable vapes canada", "elfbar bc5000")
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=["best disposable vapes canada", "elfbar bc5000"],
        path_to_canonical={},
    )
    assert gaps == []


def test_compliance_flags_each_missing_keyword():
    body = _body_with("best disposable vapes canada")  # missing the second keyword
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=["best disposable vapes canada", "elfbar bc5000"],
        path_to_canonical={},
    )
    assert any("elfbar bc5000" in g for g in gaps)
    assert not any("best disposable vapes canada" in g for g in gaps)


def test_compliance_accepts_legacy_string_signature():
    """Old call sites that pass a single string must still work."""
    body = _body_with("vape pods canada")
    gaps_present = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body="vape pods canada",
        path_to_canonical={},
    )
    assert gaps_present == []
    body_missing = _body_with("something else")
    gaps_missing = validate_article_draft_compliance(
        body_html=body_missing,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body="vape pods canada",
        path_to_canonical={},
    )
    assert any("vape pods canada" in g for g in gaps_missing)


def test_compliance_dedupes_case_insensitively():
    """Same keyword in different cases is checked once."""
    body = _body_with("disposable vapes")
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=["Disposable Vapes", "disposable vapes", "DISPOSABLE VAPES"],
        path_to_canonical={},
    )
    assert gaps == []


def test_compliance_skips_empty_and_none_entries():
    body = _body_with("disposable vapes")
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=["disposable vapes", "", None, "  "],
        path_to_canonical={},
    )
    assert gaps == []


def test_compliance_none_signals_no_keyword_check():
    """None means: don't enforce any required keyword."""
    body = _body_with("anything at all here that has nothing to do with the topic")
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=None,
        path_to_canonical={},
    )
    assert gaps == []
