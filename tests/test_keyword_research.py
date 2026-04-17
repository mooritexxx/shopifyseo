import sqlite3

from backend.app.services.keyword_research import (
    classify_intent,
    classify_ranking_status,
    compute_opportunity,
    deduplicate_results,
    match_gsc_queries,
    merge_with_existing,
    normalize_opportunity_scores,
)
from backend.app.services.keyword_research.keyword_db import TARGET_KEY, load_target_keywords
from shopifyseo.dashboard_google import get_service_setting


def test_compute_opportunity_low_difficulty_high_volume():
    score = compute_opportunity(volume=1000, traffic_potential=2000, difficulty=5)
    assert score > 0
    assert score > compute_opportunity(volume=1000, traffic_potential=2000, difficulty=50)


def test_compute_opportunity_zero_volume():
    score = compute_opportunity(volume=0, traffic_potential=500, difficulty=10)
    assert score == 0.0


def test_compute_opportunity_none_traffic():
    score = compute_opportunity(volume=500, traffic_potential=None, difficulty=10)
    assert score > 0


def test_classify_intent_transactional_wins():
    intents = {"informational": True, "commercial": True, "transactional": True, "navigational": False, "branded": False}
    intent, content_type = classify_intent(intents)
    assert intent == "transactional"
    assert content_type == "Product / Collection page"


def test_classify_intent_commercial():
    intents = {"informational": True, "commercial": True, "transactional": False, "navigational": False, "branded": False}
    intent, content_type = classify_intent(intents)
    assert intent == "commercial"
    assert content_type == "Comparison / Buying guide"


def test_classify_intent_informational_only():
    intents = {"informational": True, "commercial": False, "transactional": False, "navigational": False, "branded": False}
    intent, content_type = classify_intent(intents)
    assert intent == "informational"
    assert content_type == "Blog / Guide"


def test_classify_intent_branded():
    intents = {"informational": False, "commercial": False, "transactional": False, "navigational": False, "branded": True}
    intent, content_type = classify_intent(intents)
    assert intent == "branded"
    assert content_type == "Brand page"


def test_classify_intent_none():
    intent, content_type = classify_intent(None)
    assert intent == "informational"
    assert content_type == "Blog / Guide"


def test_normalize_opportunity_scores():
    items = [
        {"opportunity_raw": 100},
        {"opportunity_raw": 50},
        {"opportunity_raw": 0},
    ]
    normalize_opportunity_scores(items)
    assert items[0]["opportunity"] == 100.0
    assert items[1]["opportunity"] == 50.0
    assert items[2]["opportunity"] == 0.0


def test_normalize_opportunity_scores_all_zero():
    items = [{"opportunity_raw": 0}, {"opportunity_raw": 0}]
    normalize_opportunity_scores(items)
    assert items[0]["opportunity"] == 0.0
    assert items[1]["opportunity"] == 0.0


def test_deduplicate_results():
    raw = [
        {"keyword": "vape canada", "volume": 100, "seed_keywords": {"seed1"}},
        {"keyword": "Vape Canada", "volume": 200, "seed_keywords": {"seed2"}},
        {"keyword": "other keyword", "volume": 50, "seed_keywords": {"seed1"}},
    ]
    deduped = deduplicate_results(raw)
    assert len(deduped) == 2
    vape = next(r for r in deduped if r["keyword"].lower() == "vape canada")
    assert vape["volume"] == 200
    assert set(vape["seed_keywords"]) == {"seed1", "seed2"}


def test_merge_with_existing_preserves_status():
    existing = [
        {"keyword": "vape canada", "status": "approved", "volume": 100},
        {"keyword": "old keyword", "status": "dismissed", "volume": 50},
    ]
    new_items = [
        {"keyword": "vape canada", "status": "new", "volume": 200},
        {"keyword": "fresh keyword", "status": "new", "volume": 300},
    ]
    merged = merge_with_existing(existing, new_items)
    assert len(merged) == 3
    vape = next(r for r in merged if r["keyword"] == "vape canada")
    assert vape["status"] == "approved"
    # Old keyword not in new results is preserved
    old = next(r for r in merged if r["keyword"] == "old keyword")
    assert old["status"] == "dismissed"
    assert vape["volume"] == 200
    fresh = next(r for r in merged if r["keyword"] == "fresh keyword")
    assert fresh["status"] == "new"


def test_batch_seeds_groups_of_five():
    from backend.app.services.keyword_research import _batch_seeds
    seeds = ["a", "b", "c", "d", "e", "f", "g"]
    batches = _batch_seeds(seeds)
    assert len(batches) == 2
    assert batches[0] == ["a", "b", "c", "d", "e"]
    assert batches[1] == ["f", "g"]


def test_batch_seeds_empty():
    from backend.app.services.keyword_research import _batch_seeds
    assert _batch_seeds([]) == []


def test_classify_ranking_status_page_one():
    assert classify_ranking_status(5.0) == "ranking"


def test_classify_ranking_status_quick_win():
    assert classify_ranking_status(15.0) == "quick_win"


def test_classify_ranking_status_striking_distance():
    assert classify_ranking_status(35.0) == "striking_distance"


def test_classify_ranking_status_low_visibility():
    assert classify_ranking_status(60.0) == "low_visibility"


def test_classify_ranking_status_none():
    assert classify_ranking_status(None) == "not_ranking"


def test_match_gsc_exact():
    gsc_data = {
        "elf bar canada": {"position": 15.0, "clicks": 3, "impressions": 50},
        "vape juice": {"position": 30.0, "clicks": 1, "impressions": 20},
    }
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is not None
    assert result["position"] == 15.0
    assert result["clicks"] == 3


def test_match_gsc_contains_target_in_query():
    gsc_data = {
        "best elf bar vape canada": {"position": 12.0, "clicks": 5, "impressions": 100},
    }
    result = match_gsc_queries("elf bar", gsc_data)
    assert result is not None
    assert result["position"] == 12.0


def test_match_gsc_word_overlap():
    """Multi-word query with sufficient overlap matches."""
    gsc_data = {
        "disposable vape canada": {"position": 8.0, "clicks": 10, "impressions": 200},
    }
    result = match_gsc_queries("disposable vape", gsc_data)
    assert result is not None
    assert result["position"] == 8.0


def test_match_gsc_single_word_no_false_positive():
    """Single-word keyword should NOT match unrelated multi-word queries."""
    gsc_data = {
        "great white vape": {"position": 3.0, "clicks": 0, "impressions": 1},
        "disposable vape canada": {"position": 50.0, "clicks": 0, "impressions": 5},
    }
    result = match_gsc_queries("vape", gsc_data)
    assert result is None


def test_match_gsc_best_position_wins():
    gsc_data = {
        "elf bar canada": {"position": 25.0, "clicks": 2, "impressions": 30},
        "elf bar canada review": {"position": 12.0, "clicks": 5, "impressions": 80},
    }
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is not None
    assert result["position"] == 12.0
    assert result["clicks"] == 7
    assert result["impressions"] == 110


def test_match_gsc_no_match():
    gsc_data = {
        "something unrelated": {"position": 5.0, "clicks": 10, "impressions": 100},
    }
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is None


def test_match_gsc_stop_word_no_false_positive():
    """Stop words like 'what', 'is', 'in' should not count as meaningful overlap."""
    gsc_data = {
        "what is this": {"position": 3.0, "clicks": 0, "impressions": 1},
        "price in canada": {"position": 3.0, "clicks": 0, "impressions": 5},
    }
    # "what is zyn" shares {what, is} with "what is this" — but those are stop words
    assert match_gsc_queries("what is zyn", gsc_data) is None
    # "best disposable vape in canada" shares {in, canada} with "price in canada"
    # "in" is a stop word, leaving only "canada" = 1 content word overlap — not enough
    assert match_gsc_queries("best disposable vape in canada", gsc_data) is None


def test_match_gsc_content_words_still_match():
    """Real content-word containment should still match after stop-word filtering."""
    gsc_data = {
        "disposable vape in canada": {"position": 8.0, "clicks": 10, "impressions": 200},
    }
    # "disposable vape canada" content words = {disposable, vape, canada}
    # GSC content words = {disposable, vape, canada} — shorter is fully contained
    result = match_gsc_queries("disposable vape canada", gsc_data)
    assert result is not None
    assert result["position"] == 8.0


def test_match_gsc_niche_words_no_false_positive():
    """Generic niche words like 'vape' + 'disposable' should NOT cause cross-matching."""
    gsc_data = {
        "mango disposable vape": {"position": 29.0, "clicks": 0, "impressions": 10},
        "elfbar vape canada": {"position": 54.0, "clicks": 1, "impressions": 20},
    }
    # "cannabis disposable vape" has content {cannabis, disposable, vape}
    # "mango disposable vape" has content {mango, disposable, vape}
    # shorter = {mango, disposable, vape}, "mango" NOT in {cannabis, disposable, vape}
    assert match_gsc_queries("cannabis disposable vape pens canada", gsc_data) is None
    # "thc vape juice canada" content = {thc, vape, juice, canada}
    # "elfbar vape canada" content = {elfbar, vape, canada}
    # shorter = {elfbar, vape, canada}, "elfbar" NOT in {thc, vape, juice, canada}
    assert match_gsc_queries("thc vape juice canada", gsc_data) is None


def test_match_gsc_containment_shorter_in_longer():
    """Shorter phrase's content words must ALL appear in the longer phrase."""
    gsc_data = {
        "elf bar vape canada review": {"position": 20.0, "clicks": 3, "impressions": 50},
    }
    # "elf bar canada" content = {elf, bar, canada}
    # GSC content = {elf, bar, vape, canada, review}
    # {elf, bar, canada} is a subset of {elf, bar, vape, canada, review} ✅
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is not None
    assert result["position"] == 20.0

    # "geek bar canada" content = {geek, bar, canada}
    # "geek" NOT in {elf, bar, vape, canada, review} ❌
    assert match_gsc_queries("geek bar canada", gsc_data) is None


def test_get_service_setting_null_value_returns_default():
    """SQLite rows with NULL value must not be passed to json.loads as None."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, NULL)",
        (TARGET_KEY,),
    )
    conn.commit()
    assert get_service_setting(conn, TARGET_KEY, "{}") == "{}"


def test_load_target_keywords_null_blob_returns_empty():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, NULL)",
        (TARGET_KEY,),
    )
    conn.commit()
    data = load_target_keywords(conn)
    assert data == {"last_run": None, "unit_cost": 0, "items": [], "total": 0}
