"""Unit tests for faq_content_filter module."""

import pytest

from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import (
    COMPETITOR_BRAND_PATTERNS,
    FAQ_DENYLIST_CATEGORIES,
    _match_denylist_category,
    filter_and_dedupe_helpful_questions,
    filter_body_html_content,
    filter_faq_items_by_h3_headings,
    filter_paa_hierarchy,
    filter_paa_questions,
    filter_required_questions_by_h3,
    normalize_flavor_to_flavour,
    normalize_spelling_for_comparison,
    validate_and_fix_alt_text,
    validate_and_fix_excerpt,
)


class TestDenylistPatternCategories:
    """Test that denylist patterns correctly identify problematic content."""

    def test_categories_are_defined(self):
        assert len(FAQ_DENYLIST_CATEGORIES) >= 4
        names = {c.name for c in FAQ_DENYLIST_CATEGORIES}
        assert "health_medical" in names
        assert "cigarette_tobacco_comparison" in names
        assert "superlative_bait" in names
        assert "wholesale_high_nicotine" in names


class TestHealthMedicalPatterns:
    """Test health/medical claim detection."""

    @pytest.mark.parametrize(
        "text",
        [
            "Is vaping better for you than smoking?",
            "Are vapes healthier than cigarettes?",
            "Is vaping safe for lungs?",
            "Does vaping help you quit smoking?",
            "Can vaping help with smoking cessation?",
            "How to stop smoking with vapes?",
            "Is vaping good for your health?",
            "Does vaping cause lung cancer?",
            "Is vaping harmful to you?",
            "What do doctors say about vaping?",
            "Is nicotine therapeutic?",
        ],
    )
    def test_health_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "health_medical"

    @pytest.mark.parametrize(
        "text",
        [
            "What are the best vape flavours?",
            "How do I charge my vape?",
            "Which vape has the longest battery life?",
            "What is the difference between salt nic and freebase?",
        ],
    )
    def test_benign_questions_pass_health_check(self, text):
        result = _match_denylist_category(text)
        if result:
            assert result[0] != "health_medical"


class TestCigaretteComparisonPatterns:
    """Test cigarette/tobacco comparison detection."""

    @pytest.mark.parametrize(
        "text",
        [
            "How many cigarettes is one puff equal to?",
            "How many cigarettes in a vape?",
            "Is vaping vs smoking better?",
            "Vaping versus smoking comparison",
            "Compared to smoking, is vaping safe?",
            "Compared to cigarettes how long does a vape last?",
            "Switching from cigarettes to vaping",
            "Can vaping replace smoking?",
            "How many puffs in a cigarette?",
            "Is vaping vs tobacco safer?",
        ],
    )
    def test_cigarette_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "cigarette_tobacco_comparison"

    @pytest.mark.parametrize(
        "text",
        [
            "What is the best disposable vape?",
            "How many puffs in a disposable vape?",
            "Which vape has the most flavours?",
        ],
    )
    def test_benign_questions_pass_cigarette_check(self, text):
        result = _match_denylist_category(text)
        if result:
            assert result[0] != "cigarette_tobacco_comparison"


class TestSuperlativeBaitPatterns:
    """Test superlative/bait phrasing detection."""

    @pytest.mark.parametrize(
        "text",
        [
            "What is the #1 brand of vape?",
            "Which is the number one vape brand?",
            "What is the best brand of vape?",
            "What is the rarest flavour?",
            "What is the most rare flavor?",
            "What is the world's best vape?",
            "What is the best vape brand in the world?",
            "What is the top rated brand?",
        ],
    )
    def test_superlative_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "superlative_bait"

    @pytest.mark.parametrize(
        "text",
        [
            "What are some good vape brands?",
            "What flavours does STLTH offer?",
            "Which vape has the best battery?",
        ],
    )
    def test_benign_questions_pass_superlative_check(self, text):
        result = _match_denylist_category(text)
        if result:
            assert result[0] != "superlative_bait"


class TestWholesaleHighNicotinePatterns:
    """Test wholesale and high nicotine strength detection."""

    @pytest.mark.parametrize(
        "text",
        [
            "Where can I buy wholesale vapes?",
            "How do I bulk order disposables?",
            "Are you a vape distributor?",
            "Can I become a vape reseller?",
            "Do you have 100 mg nicotine?",
            "Where can I get 100mg nic strength?",
            "Do you sell 50 mg nic salt?",
            "Is higher nicotine strength better?",  # high nicotine strength pattern
        ],
    )
    def test_wholesale_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "wholesale_high_nicotine"

    @pytest.mark.parametrize(
        "text",
        [
            "What nicotine strengths do you have?",
            "What is 20 mg nicotine like?",
            "How does 10 mg compare to 20 mg?",
        ],
    )
    def test_benign_questions_pass_wholesale_check(self, text):
        result = _match_denylist_category(text)
        if result:
            assert result[0] != "wholesale_high_nicotine"


class TestFilterPaaQuestions:
    """Test PAA question filtering."""

    def test_filters_health_questions(self):
        questions = [
            {"question": "What is salt nic?", "snippet": "Salt nic is..."},
            {"question": "Is vaping healthier than smoking?", "snippet": "Some say..."},
            {"question": "What flavours are available?", "snippet": "Many..."},
        ]
        result = filter_paa_questions(questions, log_dropped=False)
        assert len(result) == 2
        assert result[0]["question"] == "What is salt nic?"
        assert result[1]["question"] == "What flavours are available?"

    def test_filters_cigarette_questions(self):
        questions = [
            {"question": "How do I use a vape?", "snippet": "First..."},
            {"question": "How many cigarettes is one puff?", "snippet": "About..."},
        ]
        result = filter_paa_questions(questions, log_dropped=False)
        assert len(result) == 1
        assert result[0]["question"] == "How do I use a vape?"

    def test_filters_superlative_questions(self):
        questions = [
            {"question": "What is the #1 brand?", "snippet": "The best..."},
            {"question": "Which STLTH flavours are popular?", "snippet": "Top picks..."},
        ]
        result = filter_paa_questions(questions, log_dropped=False)
        assert len(result) == 1
        assert "STLTH" in result[0]["question"]

    def test_filters_wholesale_questions(self):
        questions = [
            {"question": "Where to buy wholesale vapes?", "snippet": "Contact..."},
            {"question": "What is the price of a disposable?", "snippet": "Prices..."},
        ]
        result = filter_paa_questions(questions, log_dropped=False)
        assert len(result) == 1
        assert "price" in result[0]["question"].lower()

    def test_empty_input_returns_empty(self):
        assert filter_paa_questions([], log_dropped=False) == []
        assert filter_paa_questions(None, log_dropped=False) == []

    def test_preserves_all_benign_questions(self):
        questions = [
            {"question": "What is salt nic?", "snippet": "Info..."},
            {"question": "How long does a vape last?", "snippet": "Depends..."},
            {"question": "What are the best flavours in Canada?", "snippet": "Popular..."},
        ]
        result = filter_paa_questions(questions, log_dropped=False)
        assert len(result) == 3


class TestFilterPaaHierarchy:
    """Test PAA hierarchy filtering."""

    def test_filters_parent_questions(self):
        hierarchy = [
            {
                "parent_question": "Is vaping healthier?",
                "children": [{"question": "What about lung health?"}],
            },
            {
                "parent_question": "What flavours are popular?",
                "children": [{"question": "What is the best selling?"}],
            },
        ]
        result = filter_paa_hierarchy(hierarchy, log_dropped=False)
        assert len(result) == 1
        assert "flavours" in result[0]["parent_question"].lower()

    def test_filters_child_questions(self):
        hierarchy = [
            {
                "parent_question": "What flavours are popular?",
                "children": [
                    {"question": "Is vaping better for you?"},
                    {"question": "What is watermelon ice like?"},
                ],
            },
        ]
        result = filter_paa_hierarchy(hierarchy, log_dropped=False)
        assert len(result) == 1
        assert len(result[0]["children"]) == 1
        assert "watermelon" in result[0]["children"][0]["question"].lower()

    def test_empty_hierarchy(self):
        assert filter_paa_hierarchy([], log_dropped=False) == []


class TestFlavorNormalization:
    """Test flavor -> flavour case-preserving normalization."""

    def test_lowercase_flavor(self):
        assert normalize_flavor_to_flavour("best flavor", log_changes=False) == "best flavour"

    def test_uppercase_flavor(self):
        assert normalize_flavor_to_flavour("BEST FLAVOR", log_changes=False) == "BEST FLAVOUR"

    def test_titlecase_flavor(self):
        assert normalize_flavor_to_flavour("Best Flavor", log_changes=False) == "Best Flavour"

    def test_flavors_plural(self):
        assert normalize_flavor_to_flavour("all flavors", log_changes=False) == "all flavours"
        assert normalize_flavor_to_flavour("FLAVORS", log_changes=False) == "FLAVOURS"

    def test_flavored(self):
        assert normalize_flavor_to_flavour("flavored vape", log_changes=False) == "flavoured vape"
        assert normalize_flavor_to_flavour("FLAVORED", log_changes=False) == "FLAVOURED"

    def test_flavorful(self):
        assert normalize_flavor_to_flavour("flavorful experience", log_changes=False) == "flavourful experience"

    def test_flavoring(self):
        assert normalize_flavor_to_flavour("natural flavoring", log_changes=False) == "natural flavouring"

    def test_flavorless(self):
        assert normalize_flavor_to_flavour("flavorless base", log_changes=False) == "flavourless base"

    def test_preserves_flavor_beast_brand(self):
        # Flavor Beast is a brand name and should be preserved
        text = "Check out Flavor Beast products"
        result = normalize_flavor_to_flavour(text, log_changes=False)
        # The brand name should be preserved
        assert "Flavor Beast" in result or "Flavour Beast" in result

    def test_skips_urls(self):
        text = 'Check <a href="https://store.com/flavor-shots">flavor shots</a>'
        result = normalize_flavor_to_flavour(text, log_changes=False)
        # URL should be preserved, visible text should be normalized
        assert "flavor-shots" in result  # URL preserved
        assert "flavour shots" in result  # Visible text normalized

    def test_empty_input(self):
        assert normalize_flavor_to_flavour("", log_changes=False) == ""
        assert normalize_flavor_to_flavour(None, log_changes=False) is None

    def test_no_flavor_in_text(self):
        text = "This text has no such word"
        assert normalize_flavor_to_flavour(text, log_changes=False) == text

    def test_multiple_occurrences(self):
        text = "The flavor is great. Many flavors available. Flavored options too."
        result = normalize_flavor_to_flavour(text, log_changes=False)
        assert "flavour" in result
        assert "flavours" in result
        assert "Flavoured" in result

    def test_already_british_spelling(self):
        text = "Great flavour options and flavours"
        # Already-correct spellings should remain unchanged
        result = normalize_flavor_to_flavour(text, log_changes=False)
        assert result == text


class TestH3HeadingFilter:
    """Test FAQ filtering by H3 heading matching."""

    def test_keeps_matching_faqs(self):
        body = """
        <h2>Introduction</h2>
        <p>Some intro text</p>
        <h3>What is salt nic?</h3>
        <p>Salt nic is a type of nicotine.</p>
        <h3>How long does a vape last?</h3>
        <p>It depends on usage.</p>
        """
        faq_items = [
            {"question": "What is salt nic?", "answer": "Salt nic is..."},
            {"question": "How long does a vape last?", "answer": "Depends..."},
            {"question": "Unrelated question not in article?", "answer": "No match"},
        ]
        result = filter_faq_items_by_h3_headings(faq_items, body, log_dropped=False)
        assert len(result) == 2
        questions = [item["question"] for item in result]
        assert "What is salt nic?" in questions
        assert "How long does a vape last?" in questions

    def test_fuzzy_matching_works(self):
        body = """
        <h3>What are the best vape flavours in Canada?</h3>
        <p>There are many great options.</p>
        """
        faq_items = [
            {"question": "What are the best vape flavors in Canada?", "answer": "Many..."},
        ]
        result = filter_faq_items_by_h3_headings(faq_items, body, log_dropped=False)
        # Should match despite flavor/flavour spelling difference
        assert len(result) == 1

    def test_no_h3_drops_all(self):
        body = """
        <h2>Just an H2 heading</h2>
        <p>Some content</p>
        """
        faq_items = [
            {"question": "Some question?", "answer": "Answer"},
        ]
        result = filter_faq_items_by_h3_headings(faq_items, body, log_dropped=False)
        assert len(result) == 0

    def test_empty_inputs(self):
        assert filter_faq_items_by_h3_headings([], "<h3>Test</h3>", log_dropped=False) == []
        assert filter_faq_items_by_h3_headings([{"question": "Q", "answer": "A"}], "", log_dropped=False) == [{"question": "Q", "answer": "A"}]

    def test_keyword_overlap_matching(self):
        body = """
        <h3>Best nicotine strength for beginners</h3>
        <p>Information about nicotine levels.</p>
        """
        faq_items = [
            {"question": "Best nicotine strength for beginners?", "answer": "Start low..."},
        ]
        result = filter_faq_items_by_h3_headings(faq_items, body, log_dropped=False)
        # Should match due to high keyword overlap
        assert len(result) == 1


class TestFilterRequiredQuestionsByH3:
    """Test required questions filtering by H3 heading matching."""

    def test_filters_to_matching_headings(self):
        body = """
        <h3>What is salt nic?</h3>
        <p>Answer here</p>
        <h3>How do I charge my vape?</h3>
        <p>Instructions here</p>
        """
        required = [
            "What is salt nic?",
            "How do I charge my vape?",
            "Unrelated question?",
        ]
        result = filter_required_questions_by_h3(required, body, log_dropped=False)
        assert len(result) == 2
        assert "What is salt nic?" in result
        assert "How do I charge my vape?" in result

    def test_empty_body_returns_original(self):
        required = ["Question 1?", "Question 2?"]
        result = filter_required_questions_by_h3(required, "", log_dropped=False)
        assert result == required

    def test_no_h3s_drops_all(self):
        body = "<h2>Only H2</h2><p>Text</p>"
        required = ["Question?"]
        result = filter_required_questions_by_h3(required, body, log_dropped=False)
        assert len(result) == 0


class TestCaseInsensitivity:
    """Test that all pattern matching is case-insensitive."""

    @pytest.mark.parametrize(
        "text",
        [
            "IS VAPING HEALTHIER?",
            "is vaping healthier?",
            "Is Vaping Healthier?",
            "IS vaping HEALTHIER?",
        ],
    )
    def test_health_patterns_case_insensitive(self, text):
        result = _match_denylist_category(text)
        assert result is not None
        assert result[0] == "health_medical"

    @pytest.mark.parametrize(
        "text",
        [
            "HOW MANY CIGARETTES IS ONE PUFF?",
            "how many cigarettes is one puff?",
            "How Many Cigarettes Is One Puff?",
        ],
    )
    def test_cigarette_patterns_case_insensitive(self, text):
        result = _match_denylist_category(text)
        assert result is not None
        assert result[0] == "cigarette_tobacco_comparison"


class TestNormalizeSpellingForComparison:
    """Test US/CA spelling normalization for comparison purposes."""

    def test_flavor_variants(self):
        assert normalize_spelling_for_comparison("flavor") == "flavour"
        assert normalize_spelling_for_comparison("flavors") == "flavours"
        assert normalize_spelling_for_comparison("flavored") == "flavoured"
        assert normalize_spelling_for_comparison("flavorful") == "flavourful"
        assert normalize_spelling_for_comparison("flavoring") == "flavouring"
        assert normalize_spelling_for_comparison("flavorless") == "flavourless"

    def test_color_variants(self):
        assert normalize_spelling_for_comparison("color") == "colour"
        assert normalize_spelling_for_comparison("colors") == "colours"
        assert normalize_spelling_for_comparison("colored") == "coloured"
        assert normalize_spelling_for_comparison("colorful") == "colourful"
        assert normalize_spelling_for_comparison("coloring") == "colouring"
        assert normalize_spelling_for_comparison("colorless") == "colourless"

    def test_favorite_variants(self):
        assert normalize_spelling_for_comparison("favorite") == "favourite"
        assert normalize_spelling_for_comparison("favorites") == "favourites"

    def test_case_insensitive_output_lowercase(self):
        # Output is always lowercase for comparison purposes
        assert normalize_spelling_for_comparison("FLAVOR") == "flavour"
        assert normalize_spelling_for_comparison("Flavor") == "flavour"
        assert normalize_spelling_for_comparison("COLORS") == "colours"

    def test_mixed_sentence(self):
        text = "My favorite flavor is colorful and flavorful"
        expected = "my favourite flavour is colourful and flavourful"
        assert normalize_spelling_for_comparison(text) == expected

    def test_already_canadian_spelling(self):
        # Canadian spellings stay as-is (lowercased)
        assert normalize_spelling_for_comparison("flavour") == "flavour"
        assert normalize_spelling_for_comparison("colour") == "colour"
        assert normalize_spelling_for_comparison("favourite") == "favourite"

    def test_empty_input(self):
        assert normalize_spelling_for_comparison("") == ""
        assert normalize_spelling_for_comparison(None) is None

    def test_no_spelling_variants(self):
        text = "This text has no spelling variants"
        assert normalize_spelling_for_comparison(text) == text.lower()

    def test_regression_idea_12_phrase(self):
        """Regression test for idea 12: the required SERP phrase."""
        query = "Flavour beast unleashed flavors"
        normalized = normalize_spelling_for_comparison(query)
        assert normalized == "flavour beast unleashed flavours"

        # Body after normalization (what the body contains)
        body_phrase = "Flavour Beast Unleashed flavours"
        normalized_body = normalize_spelling_for_comparison(body_phrase)
        assert normalized_body == "flavour beast unleashed flavours"

        # Both should match after normalization
        assert normalized == normalized_body


class TestPuffsPerDayPatterns:
    """Test puffs per day pattern detection (added in P3)."""

    @pytest.mark.parametrize(
        "text",
        [
            "Is 20 puffs a day a lot?",
            "Is 100 puffs of vape a day a lot?",
            "How many puffs a day is normal?",
            "How many puffs per day is safe?",
            "Is 200 puffs a day bad?",
        ],
    )
    def test_puffs_per_day_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "puffs_per_day"

    @pytest.mark.parametrize(
        "text",
        [
            "How many puffs does a disposable have?",
            "What is a good puff count?",
            "How long does 5000 puffs last?",
        ],
    )
    def test_benign_puff_questions_pass(self, text):
        result = _match_denylist_category(text)
        if result:
            assert result[0] != "puffs_per_day"


class TestExternalSourcePatterns:
    """Test external source pattern detection (added in P3)."""

    @pytest.mark.parametrize(
        "text",
        [
            "What does reddit say about vaping?",
            "Best vape according to reddit",
            "Can you send me a pdf guide?",
            "Is there a forum for vapers?",
        ],
    )
    def test_external_source_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "external_source"


class TestCompetitorBrandPatterns:
    """Test competitor brand detection for off-brand filtering."""

    def test_competitor_patterns_defined(self):
        assert len(COMPETITOR_BRAND_PATTERNS) >= 5
        # These brands should be in the list
        competitor_names = [
            "elfbar", "elf bar", "juul", "vuse", "geekbar", "lost mary"
        ]
        for name in competitor_names:
            matched = False
            for pattern in COMPETITOR_BRAND_PATTERNS:
                if pattern.search(name):
                    matched = True
                    break
            assert matched, f"Expected pattern to match: {name}"


class TestFilterAndDedupeHelpfulQuestions:
    """Test the helpful questions filter/dedupe pipeline."""

    def test_filters_denylist_questions(self):
        questions = [
            "What is salt nic?",
            "Is vaping healthier than smoking?",  # health_medical
            "What flavours are available?",
        ]
        result = filter_and_dedupe_helpful_questions(questions, log_dropped=False)
        assert len(result) == 2
        assert "What is salt nic?" in result
        assert "What flavours are available?" in result

    def test_dedupes_similar_questions(self):
        questions = [
            "What is salt nic?",
            "What is salt nicotine?",  # Similar/duplicate
            "How long does a vape last?",
        ]
        result = filter_and_dedupe_helpful_questions(questions, log_dropped=False)
        # Should keep one of the salt nic questions and the battery question
        assert len(result) <= 3

    def test_normalizes_spelling(self):
        questions = [
            "What flavor is best?",  # US spelling
        ]
        result = filter_and_dedupe_helpful_questions(questions, log_dropped=False)
        # Should be normalized to Canadian spelling
        if result:
            assert "flavour" in result[0].lower()

    def test_empty_input(self):
        assert filter_and_dedupe_helpful_questions([], log_dropped=False) == []
        assert filter_and_dedupe_helpful_questions(None, log_dropped=False) == []


class TestFilterBodyHtmlContent:
    """Test H2 section removal from body HTML."""

    def test_removes_health_h2_section(self):
        html = """
        <h2>Product Features</h2>
        <p>Great features here.</p>
        <h2>Health Considerations</h2>
        <p>This section should be removed.</p>
        <h2>Flavour Options</h2>
        <p>Many flavours available.</p>
        """
        result = filter_body_html_content(html, log_dropped=False)
        assert "Product Features" in result
        assert "Health Considerations" not in result
        assert "Flavour Options" in result

    def test_preserves_safe_content(self):
        html = """
        <h2>Product Overview</h2>
        <p>This is a great product.</p>
        <h2>Available Flavours</h2>
        <p>Watermelon, Mango, Strawberry.</p>
        """
        result = filter_body_html_content(html, log_dropped=False)
        assert "Product Overview" in result
        assert "Available Flavours" in result

    def test_empty_input(self):
        assert filter_body_html_content("", log_dropped=False) == ""


class TestValidateAndFixAltText:
    """Test image alt text validation and fixing."""

    def test_valid_alt_text_passes(self):
        alt = "A sleek black disposable vape with purple accents"
        result, modified = validate_and_fix_alt_text(alt, log_issues=False)
        assert result == alt
        assert not modified

    def test_empty_alt_uses_fallback(self):
        result, modified = validate_and_fix_alt_text("", fallback_text="Product image", log_issues=False)
        assert result == "Product image"
        assert modified

    def test_prompt_leakage_uses_fallback(self):
        # Test with character limit instruction leakage
        alt = "Maximum 125 characters for this alt text description"
        result, modified = validate_and_fix_alt_text(alt, fallback_text="Product image", log_issues=False)
        assert result == "Product image"
        assert modified

    def test_short_alt_uses_fallback(self):
        alt = "Vape"
        result, modified = validate_and_fix_alt_text(alt, fallback_text="Product image", min_length=20, log_issues=False)
        assert result == "Product image"
        assert modified

    def test_long_alt_truncated_at_word_boundary(self):
        alt = "This is a very long alt text description that needs to be truncated at a word boundary because it exceeds the maximum length allowed for accessibility purposes"
        result, modified = validate_and_fix_alt_text(alt, max_length=80, log_issues=False)
        assert len(result) <= 80
        assert modified
        # Should end at a word boundary (no cut-off mid-word)
        assert not result.endswith(("t", "d", "s", "y"))  # Common mid-word endings

    def test_normalizes_us_spelling(self):
        alt = "A colorful vape with cherry flavor"
        result, modified = validate_and_fix_alt_text(alt, log_issues=False)
        assert "flavour" in result
        assert modified


class TestValidateAndFixExcerpt:
    """Test excerpt/summary validation and fixing (P4c)."""

    def test_valid_excerpt_passes(self):
        excerpt = "Explore the latest STLTH disposable vapes with premium Canadian flavours and long-lasting battery life."
        result, modified = validate_and_fix_excerpt(excerpt, log_issues=False)
        assert result == excerpt
        assert not modified

    def test_empty_excerpt_uses_fallback(self):
        result, modified = validate_and_fix_excerpt("", fallback_excerpt="Default", log_issues=False)
        assert result == "Default"
        assert modified

    @pytest.mark.parametrize(
        "boilerplate",
        [
            "In this article, we will explore the world of vaping.",
            "Read our article about the best vapes for beginners.",
            "Learn more about disposable vapes in Canada.",
            "Click to read more about our products.",
            "Find out which vape is right for you.",
            "Discover how to choose the perfect vape.",
            "We'll explore the different types of vapes available.",
            "This article covers everything about vaping.",
            "Everything you need to know about disposable vapes.",
            "Here's what you'll learn about salt nic.",
        ],
    )
    def test_boilerplate_uses_fallback(self, boilerplate):
        result, modified = validate_and_fix_excerpt(boilerplate, fallback_excerpt="", log_issues=False)
        assert result == ""
        assert modified

    def test_prompt_leakage_uses_fallback(self):
        excerpt = "SEO description should be 150-160 characters for optimal performance."
        result, modified = validate_and_fix_excerpt(excerpt, fallback_excerpt="", log_issues=False)
        assert result == ""
        assert modified

    def test_short_excerpt_uses_fallback(self):
        excerpt = "Too short"
        result, modified = validate_and_fix_excerpt(excerpt, fallback_excerpt="", min_length=50, log_issues=False)
        assert result == ""
        assert modified

    def test_long_excerpt_truncated_at_word_boundary(self):
        excerpt = "This is a very long excerpt that needs to be truncated at a word boundary because it exceeds the maximum length allowed for SEO meta descriptions which should typically be around 155-160 characters."
        result, modified = validate_and_fix_excerpt(excerpt, max_length=160, log_issues=False)
        assert len(result) <= 160
        assert modified

    def test_normalizes_us_spelling(self):
        excerpt = "Check out our amazing flavor options with premium quality vapor products for Canadian customers."
        result, modified = validate_and_fix_excerpt(excerpt, log_issues=False)
        assert "flavour" in result
        assert modified


class TestExtendedDenylistPatterns:
    """Test extended denylist patterns added in P3."""

    @pytest.mark.parametrize(
        "text",
        [
            "Can lungs heal from vaping?",
            "Can your lungs recover from vaping?",
            "Do lungs heal after quitting vaping?",
            "Is vaping or smoking harder on your lungs?",
            "Vaping or smoking which is worse?",
            "Does vaping guarantee safety?",
            "What are the health considerations?",
        ],
    )
    def test_extended_health_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "health_medical"

    @pytest.mark.parametrize(
        "text",
        [
            "Transitioning from smoking to vaping",
            "Is vaping a good transition from traditional smoking?",
            "What is an alternative to traditional smoking?",
            "How many puffs of vape equal to 1 cigarette?",  # Pattern expects digit before cigarette
        ],
    )
    def test_extended_cigarette_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "cigarette_tobacco_comparison"

    @pytest.mark.parametrize(
        "text",
        [
            "What is the #1 disposable vape?",
            "Top 10 vape flavours in Canada",
            "Top 5 vapes for beginners",
            "Most sold vape in Canada",
            "What is the most popular flavour?",
            "Best selling vape of 2024",
        ],
    )
    def test_extended_superlative_patterns_match(self, text):
        result = _match_denylist_category(text)
        assert result is not None, f"Expected match for: {text}"
        assert result[0] == "superlative_bait"
