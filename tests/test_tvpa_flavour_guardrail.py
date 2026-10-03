"""Tests for TVPA flavour-comparison guardrail.

Covers:
- Detector: affirmative hits, negations/contrasts, rule echo, allowed words, catalog names, HTML handling
- Prompt wiring: object_field_instructions, single_field_specific_instructions, field_system_prompt, field_user_prompt
- QA integration: validate_tvpa_flavour_claims, article_draft_compliance
"""
import pytest

from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import (
    TVPA_FLAVOUR_RULE,
    tvpa_flavour_matches,
    tvpa_flavour_issue_messages,
)
from shopifyseo.dashboard_ai_engine_parts.qa import validate_tvpa_flavour_claims
from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    validate_article_draft_compliance,
)
from shopifyseo.dashboard_ai_engine_parts import config


@pytest.fixture
def mock_store_identity(monkeypatch):
    """Mock store identity to avoid DB access."""
    monkeypatch.setattr(config, "_STORE_IDENTITY_CACHE", ("Vapely", "example.com"))


# ---------------------------------------------------------------------------
# Detector tests: affirmative hits
# ---------------------------------------------------------------------------

AFFIRMATIVE_CASES = [
    ("Tastes like cotton candy.", "category", "candy"),
    ("A candy-like berry essence.", "category", "candy"),
    ("sugary candy undertones", "category", "candy"),
    ("a dessert-inspired blend", "category", "dessert_baked"),
    ("reminiscent of a decadent strawberry confection", "category", "candy"),
    ("a subtle, velvety dessert-like undertone", "category", "dessert_baked"),
    ("a cola-inspired fizz", "category", "soda_soft_drink"),
    ("soda-style sparkle", "category", "soda_soft_drink"),
    ("like an energy drink", "category", "energy_drink"),
    ("a cannabis-like aroma for flavour", "category", "cannabis"),
    ("a nostalgic bubblegum finish with flavour notes", "style", "nostalgic"),
    ("the perfect summer treat", "style", "treat"),
    ("customers love this flavour", "style", "testimonial"),
    ("fits seamlessly into any lifestyle", "style", "lifestyle"),
]


@pytest.mark.parametrize("sentence,expected_group,expected_key", AFFIRMATIVE_CASES)
def test_affirmative_hits(sentence, expected_group, expected_key):
    """Affirmative TVPA violations should be detected."""
    matches = tvpa_flavour_matches(sentence)
    assert len(matches) >= 1, f"Expected match for: {sentence}"
    groups = [m["group"] for m in matches]
    keys = [m["key"] for m in matches]
    assert expected_group in groups, f"Expected group '{expected_group}' in {groups}"
    assert expected_key in keys, f"Expected key '{expected_key}' in {keys}"


# ---------------------------------------------------------------------------
# Detector tests: negations and contrasts (no matches)
# ---------------------------------------------------------------------------

NEGATION_CASES = [
    "Not candy-like at all, just clean fruit.",
    "for vapers who prefer cooling fruit over dessert or cream-based alternatives.",
    "more refreshing than a heavy dessert",
    "Unlike many grape vapes that lean toward overly artificial, candy-like sweetness, this profile is crisp.",
    "While many disposables lean into overly sweet candy profiles, this keeps it real fruit.",
    "If you find dessert profiles too heavy, try this.",
    "Treat the puff count as a guide.",
    "legal age for alcohol and cannabis purchases",
    "old-school manual-fire systems",
]


@pytest.mark.parametrize("sentence", NEGATION_CASES)
def test_negations_and_contrasts_no_matches(sentence):
    """Negated/contrast phrasing should NOT trigger matches."""
    matches = tvpa_flavour_matches(sentence)
    assert matches == [], f"Unexpected match for: {sentence} -> {matches}"


# ---------------------------------------------------------------------------
# Detector tests: rule echo
# ---------------------------------------------------------------------------

def test_rule_echo_no_match():
    """The TVPA rule text itself should not trigger matches."""
    assert tvpa_flavour_matches(TVPA_FLAVOUR_RULE) == []


@pytest.mark.parametrize("sentence", [
    "Never compare a flavour to candy, confectionery, dessert or baked goods, soda or soft drinks, energy drinks, or cannabis.",
    "Avoid nostalgic, treat, testimonial and lifestyle wording.",
])
def test_literal_rule_sentences_no_match(sentence):
    """Literal rule sentences should not trigger matches."""
    assert tvpa_flavour_matches(sentence) == []


# ---------------------------------------------------------------------------
# Detector tests: allowed words
# ---------------------------------------------------------------------------

def test_allowed_words_no_match():
    """sweet, premium, best, experience are allowed and should not trigger."""
    sentence = "A sweet, premium experience — the best strawberry ice."
    assert tvpa_flavour_matches(sentence) == []


# ---------------------------------------------------------------------------
# Detector tests: catalog name allowlist
# ---------------------------------------------------------------------------

ALLOWLIST_NAMES = [
    "Flavour Beast Alpha 80k Primo Pina Colada (Iced) Disposable Vape",
    "Peaches & Cream",
    "Banana Bake",
    "Chuggin Green Dew",
    "Dragon Fruit Lemonade",
    "Cotton Clouds",
    "Birthday Confetti",
    "Crazy Cocoa",
]


def test_catalog_name_allowlist_no_matches():
    """Sentences that just name allowlisted products should not trigger."""
    for name in ALLOWLIST_NAMES:
        sentence = f"Try the {name} for a refreshing experience."
        matches = tvpa_flavour_matches(sentence, allowed_names=ALLOWLIST_NAMES)
        assert matches == [], f"Unexpected match for: {sentence} -> {matches}"


def test_catalog_name_without_allowlist_still_flags_comparisons():
    """Without the allowlist, a comparison should still be flagged."""
    sentence = "This tastes like a cola."
    matches = tvpa_flavour_matches(sentence)
    assert len(matches) >= 1
    assert any(m["key"] == "soda_soft_drink" for m in matches)


def test_pina_colada_does_not_trip_cola():
    """'Pina Colada' should not trigger 'cola' due to word boundary."""
    sentence = "Try the Pina Colada flavour."
    matches = tvpa_flavour_matches(sentence)
    assert matches == [], f"Unexpected match for: {sentence} -> {matches}"


# ---------------------------------------------------------------------------
# Detector tests: HTML handling
# ---------------------------------------------------------------------------

def test_html_p_h2_matches_found():
    """Matches inside <p> and <h2> tags should be found."""
    html = "<h2>Flavour Profile</h2><p>A candy-like berry essence.</p>"
    matches = tvpa_flavour_matches(html)
    assert len(matches) >= 1
    assert any("candy" in m["term"].lower() for m in matches)


def test_script_json_ld_ignored():
    """Text inside <script type="application/ld+json"> should be ignored."""
    html = '<script type="application/ld+json">{"description": "candy-like flavour"}</script><p>Plain fruit notes.</p>'
    matches = tvpa_flavour_matches(html)
    assert matches == []


# ---------------------------------------------------------------------------
# Prompt wiring tests
# ---------------------------------------------------------------------------

def test_object_field_instructions_contains_rule(mock_store_identity):
    """object_field_instructions for product/collection/blog_article should contain the TVPA rule."""
    from shopifyseo.dashboard_ai_engine_parts.prompts import object_field_instructions
    
    for obj_type in ["product", "collection", "blog_article"]:
        instructions = object_field_instructions(obj_type, conn=None)
        assert TVPA_FLAVOUR_RULE in instructions, f"TVPA rule missing from {obj_type} instructions"


def test_single_field_specific_instructions_contains_rule(mock_store_identity):
    """single_field_specific_instructions for body should contain the TVPA rule."""
    from shopifyseo.dashboard_ai_engine_parts.prompts import single_field_specific_instructions
    
    for obj_type in ["product", "collection"]:
        for field in ["body", "seo_description"]:
            instructions = single_field_specific_instructions(obj_type, field, conn=None)
            assert TVPA_FLAVOUR_RULE in instructions, f"TVPA rule missing from {obj_type}/{field}"


def test_field_system_prompt_body_contains_rule(mock_store_identity):
    """field_system_prompt for body should contain the TVPA rule."""
    from shopifyseo.dashboard_ai_engine_parts.prompts import field_system_prompt
    
    for obj_type in ["product", "collection", "blog_article"]:
        prompt = field_system_prompt(obj_type, "body", "balanced", conn=None)
        assert TVPA_FLAVOUR_RULE in prompt, f"TVPA rule missing from {obj_type} body field_system_prompt"


def test_field_user_prompt_contains_rule(mock_store_identity):
    """field_user_prompt for body should contain the TVPA rule."""
    from shopifyseo.dashboard_ai_engine_parts.prompts import field_user_prompt
    
    # Minimal context and accepted_fields to render without DB
    context = {
        "fact": {"priority": 1, "gsc_impressions": 0},
        "detail": {"product": {"title": "Test Product", "vendor": "Test"}},
        "object_type": "product",
    }
    accepted_fields = {"seo_title": "Test Title", "seo_description": "Test description"}
    prompt_context_dict = {
        "primary_object": {
            "title": "Test Product",
            "specs": {"brand": "Test"},
            "intent": {"flavor_family": "fruit"},
        },
        "object_type": "product",
    }
    
    prompt = field_user_prompt(
        "product",
        "body",
        context,
        accepted_fields,
        "v3",
        prompt_context_dict=prompt_context_dict,
        signal_narrative_str="Test narrative",
        conn=None,
    )
    assert TVPA_FLAVOUR_RULE in prompt


def test_context_flavor_family_sweet_not_candy(mock_store_identity):
    """Products with candy-related titles should get flavor_family='sweet', not 'candy'.
    
    The classification order is: cooling > fruit > sweet > other
    So we need titles that have candy/cola/gummy/bubblegum but NOT ice/mint (cooling)
    and NOT berry/mango/apple/grape/peach/lemon/banana/cherry (fruit).
    """
    from shopifyseo.dashboard_ai_engine_parts.context import infer_product_intent
    
    # Test with a Cola title (without ice/mint or fruit words)
    context = {
        "detail": {
            "product": {"title": "Flavour Beast Classic Cola Disposable Vape"},
        },
        "gsc_query_clusters": [],
    }
    result = infer_product_intent(context)
    assert result["flavor_family"] == "sweet", f"Expected 'sweet', got {result['flavor_family']}"
    
    # Test with a Gummy title (without ice/mint or fruit words)
    context = {
        "detail": {
            "product": {"title": "ELFBAR Blue Gummy Vape"},
        },
        "gsc_query_clusters": [],
    }
    result = infer_product_intent(context)
    assert result["flavor_family"] == "sweet", f"Expected 'sweet', got {result['flavor_family']}"
    
    # Test with a Bubblegum title
    context = {
        "detail": {
            "product": {"title": "Level X Bubblegum Blast Disposable"},
        },
        "gsc_query_clusters": [],
    }
    result = infer_product_intent(context)
    assert result["flavor_family"] == "sweet", f"Expected 'sweet', got {result['flavor_family']}"
    
    # Test with a Candy title
    context = {
        "detail": {
            "product": {"title": "STLTH Cotton Candy Dream Vape"},
        },
        "gsc_query_clusters": [],
    }
    result = infer_product_intent(context)
    assert result["flavor_family"] == "sweet", f"Expected 'sweet', got {result['flavor_family']}"


# ---------------------------------------------------------------------------
# QA integration tests
# ---------------------------------------------------------------------------

def test_validate_tvpa_flavour_claims_affirmative():
    """validate_tvpa_flavour_claims should return (False, [...]) for affirmative body."""
    body = "This vape delivers a luscious candy-like berry essence."
    passed, issues = validate_tvpa_flavour_claims(body)
    assert not passed
    assert len(issues) >= 1


def test_validate_tvpa_flavour_claims_contrast_only():
    """validate_tvpa_flavour_claims should return (True, []) for contrast-only body."""
    body = "Unlike candy-flavoured vapes, this delivers clean fruit notes."
    passed, issues = validate_tvpa_flavour_claims(body)
    assert passed
    assert issues == []


def test_validate_article_draft_compliance_tvpa_check_true():
    """validate_article_draft_compliance with check_tvpa_flavour=True should return TVPA gaps."""
    body = "<p>A candy-like berry essence.</p>" + "<p>Details.</p>" * 100
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=None,
        path_to_canonical={},
        check_tvpa_flavour=True,
    )
    tvpa_gaps = [g for g in gaps if g.startswith("TVPA flavour wording: ")]
    assert len(tvpa_gaps) >= 1


def test_validate_article_draft_compliance_tvpa_check_false():
    """validate_article_draft_compliance with check_tvpa_flavour=False should not return TVPA gaps."""
    body = "<p>A candy-like berry essence.</p>" + "<p>Details.</p>" * 100
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=None,
        path_to_canonical={},
        check_tvpa_flavour=False,
    )
    tvpa_gaps = [g for g in gaps if g.startswith("TVPA flavour wording: ")]
    assert len(tvpa_gaps) == 0


# ---------------------------------------------------------------------------
# Issue message formatting
# ---------------------------------------------------------------------------

def test_tvpa_flavour_issue_messages():
    """tvpa_flavour_issue_messages should return human-readable messages."""
    matches = [
        {"group": "category", "key": "candy", "term": "candy-like", "sentence": "A candy-like berry."},
    ]
    messages = tvpa_flavour_issue_messages(matches)
    assert len(messages) == 1
    assert "TVPA flavour wording:" in messages[0]
    assert "'candy-like'" in messages[0]
    assert "(candy)" in messages[0]


# ---------------------------------------------------------------------------
# Article draft system prompts contain rule
# ---------------------------------------------------------------------------

def test_article_draft_system_prompts_contain_rule():
    """The _article_draft module should import and use TVPA_FLAVOUR_RULE."""
    from shopifyseo.dashboard_ai_engine_parts import _article_draft
    # Just check the import exists
    assert hasattr(_article_draft, "TVPA_FLAVOUR_RULE")


# ---------------------------------------------------------------------------
# B1: TVPA-only body passes on attempt 0 with zero repair calls
# ---------------------------------------------------------------------------

def test_split_tvpa_gaps():
    """split_tvpa_gaps should correctly separate TVPA warnings from hard gaps (B1 fix).
    
    This test uses the real split_tvpa_gaps helper from tvpa_flavour.
    """
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import split_tvpa_gaps, TVPA_GAP_PREFIX
    
    # Test with mixed gaps
    gaps = [
        "TVPA flavour wording: 'candy' (candy) in: 'A candy-like taste.'",
        "Missing required link to /collections/foo",
        "TVPA flavour wording: 'dessert' (dessert_baked) in: 'A dessert-inspired blend.'",
        "Body is too short",
    ]
    
    tvpa_w, hard = split_tvpa_gaps(gaps)
    
    assert len(tvpa_w) == 2, f"Expected 2 TVPA warnings, got {len(tvpa_w)}"
    assert len(hard) == 2, f"Expected 2 hard gaps, got {len(hard)}"
    assert all(g.startswith(TVPA_GAP_PREFIX) for g in tvpa_w)
    assert not any(g.startswith(TVPA_GAP_PREFIX) for g in hard)
    
    # Test with TVPA-only gaps
    tvpa_only_gaps = [
        "TVPA flavour wording: 'candy' (candy) in: 'A candy-like taste.'",
    ]
    tvpa_w2, hard2 = split_tvpa_gaps(tvpa_only_gaps)
    
    assert len(tvpa_w2) == 1
    assert len(hard2) == 0, "TVPA-only gaps should have no hard gaps"
    
    # Test with no gaps
    tvpa_w3, hard3 = split_tvpa_gaps([])
    assert tvpa_w3 == []
    assert hard3 == []


# ---------------------------------------------------------------------------
# B2: Flavour extraction from title
# ---------------------------------------------------------------------------

def test_extract_flavour_from_title():
    """extract_flavour_from_title should extract flavour part from product titles."""
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import extract_flavour_from_title
    
    # Basic extraction
    assert extract_flavour_from_title("ELFBAR BC5000 - Bubblegum Ice Disposable Vape") == "Bubblegum Ice"
    assert extract_flavour_from_title("Lost Mary OS5000 - Strawberry Sundae (Iced)") == "Strawberry Sundae"
    assert extract_flavour_from_title("Juul Pods - Virginia Tobacco") == "Virginia Tobacco"
    
    # No separator - returns None
    assert extract_flavour_from_title("Simple Product Name") is None
    
    # Strip various suffixes
    assert extract_flavour_from_title("Device - Blue Raspberry Vape Pod") == "Blue Raspberry"
    assert extract_flavour_from_title("Device - Grape E-Liquid") == "Grape"


def test_flavour_allowlist_prevents_false_positive():
    """Short flavour names from title should be allowlisted (B2 fix)."""
    # Title: "ELFBAR BC5000 - Bubblegum Ice Disposable Vape"
    # Body mentions "Bubblegum Ice flavour" - should NOT match
    title = "ELFBAR BC5000 - Bubblegum Ice Disposable Vape"
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import extract_flavour_from_title
    
    flavour = extract_flavour_from_title(title)
    assert flavour == "Bubblegum Ice"
    
    body = "The Bubblegum Ice flavour is smooth and refreshing."
    matches = tvpa_flavour_matches(body, allowed_names=[title, flavour])
    assert matches == [], f"Should not flag allowlisted flavour name: {matches}"
    
    # But "tastes like bubblegum candy" should still match
    body_violation = "This tastes like bubblegum candy."
    matches_violation = tvpa_flavour_matches(body_violation, allowed_names=[title, flavour])
    assert len(matches_violation) >= 1, "Should flag 'candy' comparison"


# ---------------------------------------------------------------------------
# N4: Generation retry path test with fake provider
# ---------------------------------------------------------------------------

def test_generate_single_field_core_preserves_tvpa_feedback():
    """_generate_single_field_core should preserve TVPA feedback through metadata correction (N3 fix)."""
    # This tests that original feedback (including TVPA feedback) is not overwritten
    # by metadata correction loop. We mock the internal functions.
    from unittest.mock import patch, MagicMock
    from shopifyseo.dashboard_ai_engine_parts.generation import _generate_single_field_core
    
    tvpa_feedback = "Remove TVPA violations: 'candy-like' is prohibited."
    call_count = [0]
    received_feedback = []
    
    def mock_attempt(**kwargs):
        call_count[0] += 1
        received_feedback.append(kwargs.get("retry_feedback", ""))
        # Return a value that triggers metadata issues on first call
        if call_count[0] == 1:
            return {"value": "x" * 10}  # Too short, will trigger error-severity metadata issues
        return {"value": "A great product description that meets length requirements."}
    
    def mock_metadata_issues(obj_type, data):
        if call_count[0] == 1:
            # Must include severity='error' to trigger retry (warnings don't trigger retries)
            return [{"field": "seo_description", "severity": "error", "message": "Description too short"}]
        return []
    
    with patch("shopifyseo.dashboard_ai_engine_parts.generation._generate_single_field_attempt", mock_attempt):
        with patch("shopifyseo.seo_quality.metadata_issues", mock_metadata_issues):
            result = _generate_single_field_core(
                settings={},
                context={},
                object_type="product",
                field="seo_description",
                accepted_fields={},
                prompt_context_precomputed={},
                signal_narrative_precomputed="",
                retry_feedback=tvpa_feedback,
            )
    
    # Should have called attempt twice (first failed metadata, second passed)
    assert call_count[0] == 2
    # First call should have original TVPA feedback
    assert tvpa_feedback in received_feedback[0]
    # Second call should preserve TVPA feedback and add metadata issues
    assert tvpa_feedback in received_feedback[1], f"TVPA feedback lost: {received_feedback[1]}"
    assert "too short" in received_feedback[1].lower()


def test_body_retry_rejects_worse_tvpa_issues():
    """Body retry should be rejected if TVPA issues get worse (N1 fix).
    
    This test uses the real _body_retry_acceptable helper from generation.py.
    """
    from shopifyseo.dashboard_ai_engine_parts.generation import _body_retry_acceptable
    
    # Test case: TVPA gets worse, but score and spec improve - should REJECT
    result = _body_retry_acceptable(
        body_score=0.6,
        retry_body_score=0.7,  # Better
        spec_claim_issues=["issue1"],
        retry_spec_issues=[],  # Better
        tvpa_category_issues=["tvpa1"],
        retry_tvpa_category_issues=["tvpa1", "tvpa2"],  # Worse
    )
    assert not result, "Should reject because TVPA got worse"
    
    # Test case: Spec gets worse, but score and TVPA improve - should REJECT
    result2 = _body_retry_acceptable(
        body_score=0.6,
        retry_body_score=0.7,  # Better
        spec_claim_issues=[],
        retry_spec_issues=["new_issue"],  # Worse
        tvpa_category_issues=["tvpa1"],
        retry_tvpa_category_issues=[],  # Better
    )
    assert not result2, "Should reject because spec got worse"
    
    # Test case: All improve - should accept
    result3 = _body_retry_acceptable(
        body_score=0.6,
        retry_body_score=0.7,
        spec_claim_issues=["issue1"],
        retry_spec_issues=[],
        tvpa_category_issues=["tvpa1"],
        retry_tvpa_category_issues=[],
    )
    assert result3, "Should accept when all metrics improve or stay same"
    
    # Test case: Score improves, others stay same - should accept
    result4 = _body_retry_acceptable(
        body_score=0.6,
        retry_body_score=0.8,
        spec_claim_issues=[],
        retry_spec_issues=[],
        tvpa_category_issues=[],
        retry_tvpa_category_issues=[],
    )
    assert result4, "Should accept when score improves and others are unchanged"
    
    # Test case: Nothing improves - should reject
    result5 = _body_retry_acceptable(
        body_score=0.7,
        retry_body_score=0.7,  # Same
        spec_claim_issues=["issue1"],
        retry_spec_issues=["issue1"],  # Same
        tvpa_category_issues=["tvpa1"],
        retry_tvpa_category_issues=["tvpa1"],  # Same
    )
    assert not result5, "Should reject when nothing improves"


# ---------------------------------------------------------------------------
# N6: seo_description system prompt contains TVPA rule
# ---------------------------------------------------------------------------

def test_seo_description_system_prompt_contains_tvpa_rule(mock_store_identity):
    """field_system_prompt for seo_description should include TVPA_FLAVOUR_RULE."""
    from shopifyseo.dashboard_ai_engine_parts.prompts import field_system_prompt
    
    prompt = field_system_prompt("product", "seo_description", "default")
    assert "Flavour compliance" in prompt
    assert "candy" in prompt.lower()
    assert "dessert" in prompt.lower()


# ---------------------------------------------------------------------------
# B3: Trigger-word allowlist guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sentence,allowed_names", [
    # Trigger-only names should NOT suppress detection
    ("It tastes like candy.", ["Candy"]),
    ("Tastes like Candy.", ["Candy"]),
    ("A Candy-like finish.", ["Candy"]),
    ("A dessert-like flavour.", ["Dessert"]),
    # Short names (< 3 chars) should be ignored
    ("It tastes like candy.", ["a"]),
    # Names that are part of larger trigger phrases
    ("It tastes like ice cream.", ["Ice"]),
    ("It tastes like ice cream.", ["Ice Cream"]),
    ("Smooth like a cake.", ["Cake"]),
    # Flavour name + trigger word in comparison
    ("It tastes like a cola.", ["Cola Ice"]),
    ("This tastes like bubblegum candy.", ["Bubblegum Ice"]),
])
def test_b3_trigger_word_allowlist_still_flags(sentence, allowed_names):
    """Trigger-word allowlist should NOT suppress detection for comparisons (B3 fix)."""
    matches = tvpa_flavour_matches(sentence, allowed_names=allowed_names)
    assert len(matches) >= 1, f"Should flag: '{sentence}' with allowed_names={allowed_names}"


@pytest.mark.parametrize("sentence,allowed_names", [
    # Non-trigger flavour names should be allowlisted
    ("The Bubblegum flavour is smooth.", ["Bubblegum"]),
    ("The Bubblegum Ice flavour is smooth.", ["Bubblegum Ice"]),
    ("Peaches & Cream is smooth.", ["Peaches & Cream"]),
    ("Pick the Strawberry Sundae today.", ["Strawberry Sundae"]),
    # Trigger-only names used as brand references (not comparisons)
    ("Try Candy from Brand X.", ["Candy"]),
    # Rule text should never trigger
    (TVPA_FLAVOUR_RULE, []),
])
def test_b3_trigger_word_allowlist_stays_clean(sentence, allowed_names):
    """Allowlisted names that aren't comparisons should stay clean (B3 fix)."""
    matches = tvpa_flavour_matches(sentence, allowed_names=allowed_names)
    assert matches == [], f"Should NOT flag: '{sentence}' with allowed_names={allowed_names} -> {matches}"


def test_b3_is_trigger_only():
    """_is_trigger_only should identify names made only of trigger words."""
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import _is_trigger_only
    
    # These are trigger-only (no non-trigger words >= 3 chars)
    assert _is_trigger_only("Candy") is True
    assert _is_trigger_only("Dessert") is True
    assert _is_trigger_only("Ice Cream") is True
    assert _is_trigger_only("Bubblegum") is True  # "bubblegum" is a trigger word
    
    # These have non-trigger words
    assert _is_trigger_only("Bubblegum Ice") is False  # "Ice" is short but "Bubblegum" is there
    assert _is_trigger_only("Peaches & Cream") is False
    assert _is_trigger_only("Strawberry Sundae") is False
    assert _is_trigger_only("Virginia Tobacco") is False


def test_b3_whole_word_matching():
    """_mask_allowed should only mask whole-word matches, not substrings."""
    # "Ice" should not mask the "ice" in "ice cream"
    matches = tvpa_flavour_matches("It tastes like ice cream.", allowed_names=["Ice"])
    assert len(matches) >= 1, "Should flag 'ice cream' even with 'Ice' allowlisted"
    
    # "a" should be ignored (less than 3 chars)
    matches = tvpa_flavour_matches("It tastes like candy.", allowed_names=["a"])
    assert len(matches) >= 1, "Should flag 'candy' even with 'a' allowlisted"


# ---------------------------------------------------------------------------
# Test _finalize_and_repair_body loop behavior
# ---------------------------------------------------------------------------

def test_finalize_and_repair_body_tvpa_only_no_repair():
    """_finalize_and_repair_body with TVPA-only gaps should pass with 0 repair calls.
    
    This simulates the real loop behavior with stubbed compliance check and repair.
    """
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import split_tvpa_gaps
    
    # Track repair calls
    repair_calls = []
    
    def mock_compliance_gaps(body, *, faq_candidates_rejected=False):
        # Return TVPA-only gaps
        return ["TVPA flavour wording: 'candy' (candy) in: 'A candy-like taste.'"]
    
    def mock_append_repair_html(body, gaps, title):
        repair_calls.append(gaps)
        return body + "<p>Repaired</p>"
    
    # Simulate the _finalize_and_repair_body loop
    body = "<p>Test body with candy-like content.</p>"
    for attempt in range(3):
        gaps = mock_compliance_gaps(body)
        tvpa_w, hard = split_tvpa_gaps(gaps)
        
        if not hard:
            # Pass: no hard gaps
            validation = {"ok": True, "repairs": attempt, "tvpa_flavour_warnings": tvpa_w}
            break
        
        if attempt >= 2:
            break
        
        # Only repair hard gaps
        body = mock_append_repair_html(body, hard, "Test Title")
    
    # Verify: 0 repair calls, validation['repairs'] == 0
    assert len(repair_calls) == 0, f"Expected 0 repair calls, got {len(repair_calls)}"
    assert validation["repairs"] == 0, f"Expected repairs=0, got {validation['repairs']}"
    assert len(validation["tvpa_flavour_warnings"]) == 1


def test_finalize_and_repair_body_hard_gap_calls_repair():
    """_finalize_and_repair_body with hard gaps should call repair.
    
    This simulates the real loop behavior with stubbed compliance check and repair.
    """
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import split_tvpa_gaps
    
    # Track repair calls
    repair_calls = []
    call_count = [0]
    
    def mock_compliance_gaps(body, *, faq_candidates_rejected=False):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: return one hard gap
            return ["Missing required link to /collections/foo"]
        # After repair: return no gaps
        return []
    
    def mock_append_repair_html(body, gaps, title):
        repair_calls.append(gaps)
        return body + "<p>Repaired link</p>"
    
    # Simulate the _finalize_and_repair_body loop
    body = "<p>Test body.</p>"
    validation = None
    for attempt in range(3):
        gaps = mock_compliance_gaps(body)
        tvpa_w, hard = split_tvpa_gaps(gaps)
        
        if not hard:
            # Pass: no hard gaps
            validation = {"ok": True, "repairs": attempt, "tvpa_flavour_warnings": tvpa_w}
            break
        
        if attempt >= 2:
            break
        
        # Only repair hard gaps
        body = mock_append_repair_html(body, hard, "Test Title")
    
    # Verify: 1 repair call (hard gap), then pass on attempt 1
    assert len(repair_calls) == 1, f"Expected 1 repair call, got {len(repair_calls)}"
    assert repair_calls[0] == ["Missing required link to /collections/foo"]
    assert validation is not None
    assert validation["repairs"] == 1, f"Expected repairs=1, got {validation['repairs']}"


# ---------------------------------------------------------------------------
# Test variant title allowlist from detail_payload['variants']
# ---------------------------------------------------------------------------

def test_variant_title_allowlist_from_detail_payload():
    """Variant titles from detail_payload['variants'] should be allowlisted (but not hide comparisons)."""
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import extract_flavour_from_title
    
    # Simulate the variant allowlist building logic from generation.py
    detail_payload = {
        "product": {"title": "ELFBAR BC5000 - Bubblegum Ice Disposable Vape"},
        "variants": [
            {"title": "3mg Nicotine - Bubblegum Ice"},
            {"title": "6mg Nicotine - Bubblegum Ice"},
        ],
    }
    
    # Build allowlist as generation.py does
    tvpa_allowed_names = []
    primary = detail_payload.get("product") or {}
    if primary.get("title"):
        title_str = str(primary["title"])
        tvpa_allowed_names.append(title_str)
        flavour = extract_flavour_from_title(title_str)
        if flavour:
            tvpa_allowed_names.append(flavour)
    
    # Note: variants come from detail_payload, not primary
    variants = detail_payload.get("variants") or []
    for var in variants:
        if isinstance(var, dict) and var.get("title"):
            var_title = str(var["title"])
            tvpa_allowed_names.append(var_title)
            var_flavour = extract_flavour_from_title(var_title)
            if var_flavour:
                tvpa_allowed_names.append(var_flavour)
    
    # The allowlist should contain the variant titles
    assert "3mg Nicotine - Bubblegum Ice" in tvpa_allowed_names
    assert "6mg Nicotine - Bubblegum Ice" in tvpa_allowed_names
    
    # Variant mention should be clean
    body = "Choose the 3mg Nicotine variant for a smooth experience."
    matches = tvpa_flavour_matches(body, allowed_names=tvpa_allowed_names)
    assert matches == [], f"Should not flag variant mention: {matches}"
    
    # But comparison should still be flagged
    body_comparison = "This tastes like bubblegum candy."
    matches_comparison = tvpa_flavour_matches(body_comparison, allowed_names=tvpa_allowed_names)
    assert len(matches_comparison) >= 1, "Should flag 'candy' comparison"
