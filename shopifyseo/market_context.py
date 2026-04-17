"""Primary-market helpers used across AI prompts, keyword research, and data layers.

All market-specific logic (spelling, geo modifiers, subnational guidance, etc.)
is centralised here so prompt files never hardcode a country name.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_PRIMARY_MARKET_CACHE: str | None = None

SUPPORTED_COUNTRIES: dict[str, str] = {
    "CA": "Canada",
    "US": "United States",
    "GB": "United Kingdom",
    "AU": "Australia",
    "NZ": "New Zealand",
    "IE": "Ireland",
    "ZA": "South Africa",
    "IN": "India",
    "SG": "Singapore",
    "AE": "United Arab Emirates",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "JP": "Japan",
    "BR": "Brazil",
    "MX": "Mexico",
}

_DEFAULT_CODE = "CA"

# ---------------------------------------------------------------------------
# Core lookups
# ---------------------------------------------------------------------------

def get_primary_country_code(conn: sqlite3.Connection) -> str:
    """Read primary_market_country from DB, validate, fallback to CA."""
    global _PRIMARY_MARKET_CACHE
    if _PRIMARY_MARKET_CACHE is not None:
        return _PRIMARY_MARKET_CACHE
    from shopifyseo.dashboard_google import get_service_setting
    raw = (get_service_setting(conn, "primary_market_country") or "").strip().upper()
    code = raw if raw in SUPPORTED_COUNTRIES else _DEFAULT_CODE
    _PRIMARY_MARKET_CACHE = code
    return code


def country_display_name(code: str) -> str:
    return SUPPORTED_COUNTRIES.get(code.upper(), SUPPORTED_COUNTRIES[_DEFAULT_CODE])


_LANG_REGION_MAP: dict[str, str] = {
    "CA": "en-CA", "US": "en-US", "GB": "en-GB", "AU": "en-AU",
    "NZ": "en-NZ", "IE": "en-IE", "ZA": "en-ZA", "IN": "en-IN",
    "SG": "en-SG", "AE": "en-AE", "DE": "de-DE", "FR": "fr-FR",
    "IT": "it-IT", "ES": "es-ES", "NL": "nl-NL", "SE": "sv-SE",
    "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI", "JP": "ja-JP",
    "BR": "pt-BR", "MX": "es-MX",
}


def language_region_code(code: str) -> str:
    return _LANG_REGION_MAP.get(code.upper(), "en-CA")


# ---------------------------------------------------------------------------
# Spelling
# ---------------------------------------------------------------------------

_COMMONWEALTH = frozenset({"CA", "GB", "AU", "NZ", "IE", "ZA", "IN", "SG", "AE"})

_COMMONWEALTH_SPELLING = (
    "Use Commonwealth English spelling (e.g. 'flavours', 'vapour', 'favourite', 'colour', 'centre')."
)
_AMERICAN_SPELLING = (
    "Use American English spelling (e.g. 'flavors', 'vapor', 'favorite', 'color', 'center')."
)


def spelling_variant(code: str) -> str:
    c = code.upper()
    if c == "US" or c == "MX" or c == "BR":
        return _AMERICAN_SPELLING
    if c in _COMMONWEALTH:
        return _COMMONWEALTH_SPELLING
    return _COMMONWEALTH_SPELLING


# ---------------------------------------------------------------------------
# Subnational guidance
# ---------------------------------------------------------------------------

_SUBNATIONAL: dict[str, str] = {
    "CA": "provinces (e.g. Ontario, British Columbia, Alberta)",
    "US": "states (e.g. California, Texas, New York)",
    "GB": "regions (e.g. London, Manchester, Scotland)",
    "AU": "states (e.g. New South Wales, Victoria, Queensland)",
    "NZ": "regions (e.g. Auckland, Wellington, Canterbury)",
    "IE": "counties (e.g. Dublin, Cork, Galway)",
    "DE": "states (e.g. Bavaria, Berlin, North Rhine-Westphalia)",
    "FR": "regions (e.g. Île-de-France, Provence, Brittany)",
    "BR": "states (e.g. São Paulo, Rio de Janeiro, Minas Gerais)",
    "IN": "states (e.g. Maharashtra, Delhi, Karnataka)",
}


def subnational_guidance(code: str) -> str:
    return _SUBNATIONAL.get(code.upper(), f"major regions of {country_display_name(code)}")


# ---------------------------------------------------------------------------
# Shipping / availability cues
# ---------------------------------------------------------------------------

_SHIPPING_CUES: dict[str, tuple[str, str]] = {
    "CA": ("shipped across Canada", "available in Canada"),
    "US": ("ships nationwide", "available across the US"),
    "GB": ("UK-wide delivery", "available across the UK"),
    "AU": ("Australia-wide shipping", "available in Australia"),
    "NZ": ("NZ-wide shipping", "available in New Zealand"),
    "IE": ("ships across Ireland", "available in Ireland"),
}


def shipping_cue(code: str) -> tuple[str, str]:
    c = code.upper()
    name = country_display_name(c)
    return _SHIPPING_CUES.get(c, (f"ships to {name}", f"available in {name}"))


# ---------------------------------------------------------------------------
# Geographic modifier keywords (replaces context.py canada_keywords)
# ---------------------------------------------------------------------------

def geo_modifier_keywords(
    code: str,
    brand: str = "",
    flavor: str = "",
    device_type: str = "",
) -> list[str]:
    name = country_display_name(code).lower()
    short = code.lower()
    kws: list[str] = [
        f"vape shop {name}",
        f"buy vape online {name}",
        f"{name} disposable vape",
    ]
    if brand:
        kws.append(f"{brand} {name}")
    if flavor:
        kws.append(f"{flavor} vape {name}")
    if brand and flavor:
        kws.append(f"{brand} {flavor} vape {name}")
    if short != name:
        kws.append(f"vape {short}")
    return kws[:6]


# ---------------------------------------------------------------------------
# Composite prompt fragment
# ---------------------------------------------------------------------------

def build_market_prompt_fragment(conn: sqlite3.Connection) -> str:
    """Single block for system prompts: market, spelling, geo modifiers, subnational."""
    code = get_primary_country_code(conn)
    name = country_display_name(code)
    spell = spelling_variant(code)
    sub = subnational_guidance(code)
    ship_phrase, avail_phrase = shipping_cue(code)

    lines = [
        f"Primary market: {name} ({code}).",
        spell,
        (
            f"When space permits in titles, include '{name}' as a geographic modifier "
            f"to capture geo-modified commercial search queries "
            f"(e.g. 'Best Disposable Vapes {name}'). "
            f"Drop the geo modifier only if the title would exceed the character limit."
        ),
        (
            f"Where topically relevant, incorporate {sub} "
            f"to strengthen local relevance — but only when supported by data."
        ),
        (
            f"Use market-appropriate availability phrasing: "
            f"'{ship_phrase}', '{avail_phrase}'."
        ),
        (
            f"Content must be compliant for an adult-consumer store in {name}. "
            f"Do not make health, smoking cessation, or medical claims."
        ),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience: full market context dict (useful when threading conn is hard)
# ---------------------------------------------------------------------------

def market_context_dict(conn: sqlite3.Connection) -> dict[str, Any]:
    """Pre-computed market values for places that can't easily call conn repeatedly."""
    code = get_primary_country_code(conn)
    name = country_display_name(code)
    ship_phrase, avail_phrase = shipping_cue(code)
    return {
        "code": code,
        "name": name,
        "lang_region": language_region_code(code),
        "spelling": spelling_variant(code),
        "subnational": subnational_guidance(code),
        "shipping_phrase": ship_phrase,
        "availability_phrase": avail_phrase,
        "geo_keywords_fn": geo_modifier_keywords,
        "prompt_fragment": build_market_prompt_fragment(conn),
    }
