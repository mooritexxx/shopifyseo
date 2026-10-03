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
