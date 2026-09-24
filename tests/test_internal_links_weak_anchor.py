"""Tests for weak anchor phrase detection."""

import pytest

from shopifyseo.internal_links.anchors import (
    is_weak_anchor,
    get_weak_anchor_warning,
    WEAK_ANCHOR_WORDS,
)


class TestIsWeakAnchor:
    """Tests for is_weak_anchor function."""

    def test_single_generic_word_is_weak(self):
        assert is_weak_anchor("here") is True
        assert is_weak_anchor("click") is True
        assert is_weak_anchor("link") is True
        assert is_weak_anchor("buy") is True

    def test_single_domain_specific_weak_word(self):
        assert is_weak_anchor("disposable") is True
        assert is_weak_anchor("vape") is True
        assert is_weak_anchor("tank") is True
        assert is_weak_anchor("pod") is True

    def test_case_insensitive(self):
        assert is_weak_anchor("HERE") is True
        assert is_weak_anchor("Click") is True
        assert is_weak_anchor("DISPOSABLE") is True

    def test_multi_word_phrase_not_weak(self):
        assert is_weak_anchor("ceramic tanks") is False
        assert is_weak_anchor("best vape kit") is False
        assert is_weak_anchor("click here for more") is False

    def test_specific_product_name_not_weak(self):
        assert is_weak_anchor("ABT 85K") is False
        assert is_weak_anchor("Elf Bar 5000") is False

    def test_empty_or_none_is_weak(self):
        assert is_weak_anchor(None) is True
        assert is_weak_anchor("") is True
        assert is_weak_anchor("   ") is True


class TestGetWeakAnchorWarning:
    """Tests for get_weak_anchor_warning function."""

    def test_returns_warning_for_weak_anchor(self):
        warning = get_weak_anchor_warning("here")
        assert warning is not None
        assert "generic" in warning.lower()

    def test_returns_none_for_good_anchor(self):
        assert get_weak_anchor_warning("ceramic tanks") is None
        assert get_weak_anchor_warning("best disposable vapes 2024") is None

    def test_returns_warning_for_empty(self):
        assert get_weak_anchor_warning("") is not None
        assert get_weak_anchor_warning(None) is not None


class TestWeakAnchorWordList:
    """Tests for the weak anchor word list coverage."""

    def test_common_generic_words_covered(self):
        expected = {"here", "click", "link", "this", "that", "more", "read"}
        assert expected.issubset(WEAK_ANCHOR_WORDS)

    def test_vape_domain_words_covered(self):
        expected = {"disposable", "vape", "pod", "tank", "juice", "flavor"}
        assert expected.issubset(WEAK_ANCHOR_WORDS)

    def test_good_words_not_in_list(self):
        # These should not be in the weak list
        not_weak = {"ceramic", "strawberry", "2024", "review", "guide"}
        assert not any(w in WEAK_ANCHOR_WORDS for w in not_weak)
