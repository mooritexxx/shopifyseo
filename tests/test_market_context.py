"""Tests for market_context helpers."""

import sqlite3
import pytest

from shopifyseo.market_context import (
    SUPPORTED_COUNTRIES,
    build_market_prompt_fragment,
    country_display_name,
    geo_modifier_keywords,
    get_primary_country_code,
    language_region_code,
    market_context_dict,
    shipping_cue,
    spelling_variant,
    subnational_guidance,
    _PRIMARY_MARKET_CACHE,
)
import shopifyseo.market_context as mc


@pytest.fixture(autouse=True)
def _reset_cache():
    mc._PRIMARY_MARKET_CACHE = None
    yield
    mc._PRIMARY_MARKET_CACHE = None


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    return c


def _set_country(conn: sqlite3.Connection, code: str):
    mc._PRIMARY_MARKET_CACHE = None
    conn.execute(
        "INSERT OR REPLACE INTO service_settings (key, value) VALUES (?, ?)",
        ("primary_market_country", code),
    )


# --- country_display_name ---

def test_display_name_ca():
    assert country_display_name("CA") == "Canada"


def test_display_name_us():
    assert country_display_name("US") == "United States"


def test_display_name_gb():
    assert country_display_name("GB") == "United Kingdom"


def test_display_name_case_insensitive():
    assert country_display_name("au") == "Australia"


def test_display_name_unknown_falls_back():
    assert country_display_name("XX") == "Canada"


# --- market_context_dict ---

def test_market_context_dict_shape(conn):
    _set_country(conn, "US")
    ctx = market_context_dict(conn)
    assert ctx["code"] == "US"
    assert set(ctx.keys()) >= {"code", "lang_region"}
    assert ctx["lang_region"] == "en-US"


# --- language_region_code ---

def test_lang_ca():
    assert language_region_code("CA") == "en-CA"


def test_lang_us():
    assert language_region_code("US") == "en-US"


def test_lang_gb():
    assert language_region_code("GB") == "en-GB"


def test_lang_de():
    assert language_region_code("DE") == "de-DE"


def test_lang_unknown_fallback():
    assert language_region_code("XX") == "en-CA"


# --- spelling_variant ---

def test_spelling_ca_is_commonwealth():
    assert "flavours" in spelling_variant("CA")


def test_spelling_gb_is_commonwealth():
    assert "flavours" in spelling_variant("GB")


def test_spelling_us_is_american():
    assert "flavors" in spelling_variant("US")


# --- subnational_guidance ---

def test_subnational_ca():
    result = subnational_guidance("CA")
    assert "Ontario" in result
    assert "provinces" in result


def test_subnational_us():
    result = subnational_guidance("US")
    assert "California" in result
    assert "states" in result


def test_subnational_unknown():
    result = subnational_guidance("SG")
    assert "Singapore" in result


# --- shipping_cue ---

def test_shipping_ca():
    phrase, avail = shipping_cue("CA")
    assert "Canada" in phrase or "Canada" in avail


def test_shipping_us():
    phrase, avail = shipping_cue("US")
    assert "nationwide" in phrase


def test_shipping_unknown():
    phrase, avail = shipping_cue("JP")
    assert "Japan" in phrase or "Japan" in avail


# --- geo_modifier_keywords ---

def test_geo_keywords_ca():
    kws = geo_modifier_keywords("CA", brand="STLTH", flavor="Mango")
    assert any("canada" in kw for kw in kws)
    assert any("stlth" in kw.lower() for kw in kws)


def test_geo_keywords_us():
    kws = geo_modifier_keywords("US")
    assert any("united states" in kw for kw in kws)


def test_geo_keywords_max_six():
    kws = geo_modifier_keywords("CA", brand="ALLO", flavor="Grape", device_type="pod")
    assert len(kws) <= 6


# --- get_primary_country_code ---

def test_default_is_ca(conn):
    assert get_primary_country_code(conn) == "CA"


def test_reads_db_value(conn):
    _set_country(conn, "US")
    assert get_primary_country_code(conn) == "US"


def test_invalid_code_falls_back(conn):
    _set_country(conn, "INVALID")
    assert get_primary_country_code(conn) == "CA"


def test_cache_is_used(conn):
    _set_country(conn, "GB")
    assert get_primary_country_code(conn) == "GB"
    conn.execute(
        "UPDATE service_settings SET value = 'AU' WHERE key = 'primary_market_country'"
    )
    assert get_primary_country_code(conn) == "GB"


# --- build_market_prompt_fragment ---

def test_fragment_contains_country(conn):
    _set_country(conn, "CA")
    frag = build_market_prompt_fragment(conn)
    assert "Canada" in frag
    assert "CA" in frag


def test_fragment_us(conn):
    _set_country(conn, "US")
    frag = build_market_prompt_fragment(conn)
    assert "United States" in frag
    assert "flavors" in frag
    assert "states" in frag.lower()


def test_fragment_gb(conn):
    _set_country(conn, "GB")
    frag = build_market_prompt_fragment(conn)
    assert "United Kingdom" in frag
    assert "flavours" in frag
