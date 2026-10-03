"""Deterministic product SEO title and meta description token checks.

This module provides:
- `RequiredTokens`: frozen dataclass with flavour, strength and sources
- `required_product_name_tokens`: extract tokens from product context
- `build_deterministic_seo_title`: build SEO title as `<product name> | <store suffix>`
- `check_seo_title_format`: validate SEO title matches deterministic format
- `check_meta_description_tokens`: validate meta has full flavour (strength is warning)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .config import get_store_identity

__all__ = [
    "RequiredTokens",
    "required_product_name_tokens",
    "build_deterministic_seo_title",
    "check_seo_title_format",
    "check_meta_description_tokens",
    "TitleTokenOverflowError",
]

SEO_TITLE_WARNING_THRESHOLD = 60


class TitleTokenOverflowError(ValueError):
    """Raised when required tokens cannot fit within SEO title limits."""
    pass


@dataclass(frozen=True)
class RequiredTokens:
    """Required tokens extracted from product context."""
    flavour: str | None
    strength: str | None
    strength_numeric: str | None
    strength_unit: Literal["mg", "%"] | None
    sources: dict

    def flavour_fits(self, max_len: int = 65) -> bool:
        """Check if flavour (+ strength for meta) fits within limit."""
        if not self.flavour:
            return True
        return len(self.flavour) <= max_len


_STRENGTH_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mg|%)\b", re.IGNORECASE)

_PRODUCT_SUFFIXES = re.compile(
    r"\s*\(?(?:Disposable\s*Vape|Vape\s*Pod|E-Liquid|Vape|Pod)\)?\s*$",
    re.IGNORECASE,
)


def _extract_flavour_from_product_title(title: str) -> tuple[str | None, str | None]:
    """Extract flavour portion from product title (after last ' - ').
    
    Unlike tvpa_flavour.extract_flavour_from_title, this does NOT strip 'Ice'/'Iced',
    because 'Blue Razz Ice' is the full flavour name. It DOES strip strength tokens.
    
    Returns (flavour, source_description).
    """
    if " - " not in title:
        return None, None
    
    flavour_part = title.rsplit(" - ", 1)[-1].strip()
    if not flavour_part:
        return None, None
    
    flavour_part = _PRODUCT_SUFFIXES.sub("", flavour_part).strip()
    flavour_part = _STRENGTH_PATTERN.sub("", flavour_part).strip()
    flavour_part = re.sub(r"\s+", " ", flavour_part).strip()
    
    return (flavour_part, "title") if flavour_part else (None, None)


def _extract_strength_from_title(title: str) -> tuple[str | None, str | None, str | None]:
    """Extract strength token from product title.
    
    Returns (full_strength, numeric, unit) e.g. ("20mg", "20", "mg") or (None, None, None).
    """
    match = _STRENGTH_PATTERN.search(title)
    if match:
        numeric = match.group(1)
        unit = match.group(2).lower()
        full = f"{numeric}{unit}"
        return full, numeric, unit
    return None, None, None


def _get_option_value(variants: list[dict], option_name: str) -> str | None:
    """Get single consistent option value across all variants.
    
    Returns the value only if all variants have the same single value for this option.
    """
    values = set()
    option_name_lower = option_name.lower()
    
    for var in variants:
        selected_options = var.get("selected_options_json")
        if selected_options:
            import json
            try:
                if isinstance(selected_options, str):
                    opts = json.loads(selected_options)
                else:
                    opts = selected_options
                if isinstance(opts, list):
                    for opt in opts:
                        if isinstance(opt, dict) and opt.get("name", "").lower() == option_name_lower:
                            values.add(opt.get("value", ""))
            except (json.JSONDecodeError, TypeError):
                pass
    
    if len(values) == 1:
        val = values.pop()
        return val if val else None
    return None


def required_product_name_tokens(context: dict) -> RequiredTokens:
    """Extract required flavour and strength tokens from product context.
    
    Sources for flavour (first that applies):
    1. Product's 'Flavour'/'Flavor' option if all variants have same value
    2. Title flavour: text after last ' - ' with suffixes and strength stripped
    3. None (no flavour requirement)
    
    Sources for strength (first that applies):
    1. Regex on product title: \\b(\\d+(?:\\.\\d+)?)\\s*(mg|%)\\b
    2. primary['nicotine_strength'] or metafield custom.nicotine_strength
    3. Variant option named 'Nicotine'/'Strength'/'Nicotine Strength' with one value
    4. None if multiple distinct strengths or none found
    """
    detail = context.get("detail") or {}
    primary = detail.get("product") or {}
    variants = detail.get("variants") or []
    metafields = detail.get("metafields") or []
    
    sources: dict = {}
    
    # --- Flavour extraction ---
    flavour: str | None = None
    
    # 1. Check flavour option
    flavour_opt = _get_option_value(variants, "Flavour") or _get_option_value(variants, "Flavor")
    if flavour_opt:
        flavour = flavour_opt
        sources["flavour"] = "option"
    else:
        # 2. Extract from title
        title = primary.get("title", "")
        title_flavour, src = _extract_flavour_from_product_title(title)
        if title_flavour:
            flavour = title_flavour
            sources["flavour"] = src
    
    # --- Strength extraction ---
    strength: str | None = None
    strength_numeric: str | None = None
    strength_unit: Literal["mg", "%"] | None = None
    
    # 1. Regex on product title
    title = primary.get("title", "")
    str_full, str_num, str_unit = _extract_strength_from_title(title)
    if str_full:
        strength = str_full
        strength_numeric = str_num
        strength_unit = str_unit
        sources["strength"] = "title"
    else:
        # 2. Check nicotine_strength field or metafield
        nic_strength = primary.get("nicotine_strength")
        if not nic_strength:
            for mf in metafields:
                if isinstance(mf, dict):
                    ns = mf.get("namespace", "")
                    key = mf.get("key", "")
                    if ns == "custom" and key == "nicotine_strength":
                        nic_strength = mf.get("value")
                        break
        
        if nic_strength:
            match = _STRENGTH_PATTERN.search(str(nic_strength))
            if match:
                strength_numeric = match.group(1)
                strength_unit = match.group(2).lower()
                strength = f"{strength_numeric}{strength_unit}"
                sources["strength"] = "metafield"
        else:
            # 3. Check variant options
            for opt_name in ("Nicotine", "Strength", "Nicotine Strength"):
                opt_val = _get_option_value(variants, opt_name)
                if opt_val:
                    match = _STRENGTH_PATTERN.search(opt_val)
                    if match:
                        strength_numeric = match.group(1)
                        strength_unit = match.group(2).lower()
                        strength = f"{strength_numeric}{strength_unit}"
                        sources["strength"] = "variant_option"
                        break
    
    return RequiredTokens(
        flavour=flavour,
        strength=strength,
        strength_numeric=strength_numeric,
        strength_unit=strength_unit,
        sources=sources,
    )


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison: casefold, collapse whitespace, & ≡ and."""
    text = text.casefold()
    text = text.replace("&", "and")
    text = re.sub(r"[^\w]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_deterministic_seo_title(product_name: str, conn=None) -> str:
    """Build the canonical SEO title: '<product name> | <store suffix>'.
    
    The store suffix is taken from store identity (e.g., 'Vapely Canada').
    """
    store_name, _ = get_store_identity(conn)
    suffix = store_name or "Vapely"
    if "canada" not in suffix.lower():
        suffix = f"{suffix} Canada"
    
    return f"{product_name} | {suffix}"


def check_seo_title_format(
    seo_title: str,
    product_name: str,
    conn=None,
) -> tuple[list[str], list[str]]:
    """Validate SEO title matches the deterministic format.
    
    Format: '<full product name> | <store suffix>'
    - Product name must match word-for-word (case-insensitive, whitespace-collapsed)
    - Nothing may be added, dropped, reordered
    - >60 chars is a warning, not failure
    - Never truncated
    
    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []
    
    expected = build_deterministic_seo_title(product_name, conn)
    
    expected_norm = _normalize_for_comparison(expected)
    actual_norm = _normalize_for_comparison(seo_title)
    
    if expected_norm != actual_norm:
        actual_before_pipe = seo_title.rsplit("|", 1)[0].strip() if "|" in seo_title else seo_title
        expected_before_pipe = expected.rsplit("|", 1)[0].strip()
        
        actual_name_norm = _normalize_for_comparison(actual_before_pipe)
        expected_name_norm = _normalize_for_comparison(expected_before_pipe)
        
        if actual_name_norm != expected_name_norm:
            expected_words = set(expected_name_norm.split())
            actual_words = set(actual_name_norm.split())
            
            missing = expected_words - actual_words
            added = actual_words - expected_words
            
            if missing:
                errors.append(
                    f"SEO title is missing words from product name: {', '.join(sorted(missing))}. "
                    f"Expected: '{expected}'"
                )
            elif added:
                errors.append(
                    f"SEO title has extra words not in product name: {', '.join(sorted(added))}. "
                    f"Expected: '{expected}'"
                )
            else:
                errors.append(
                    f"SEO title does not match the required format. "
                    f"Expected: '{expected}', got: '{seo_title}'"
                )
        else:
            if "|" not in seo_title:
                errors.append(
                    f"SEO title missing store suffix. Expected: '{expected}'"
                )
            else:
                suffix_actual = seo_title.rsplit("|", 1)[1].strip()
                suffix_expected = expected.rsplit("|", 1)[1].strip()
                if _normalize_for_comparison(suffix_actual) != _normalize_for_comparison(suffix_expected):
                    errors.append(
                        f"SEO title has incorrect store suffix. "
                        f"Expected suffix: '{suffix_expected}', got: '{suffix_actual}'"
                    )
    
    if len(seo_title) > SEO_TITLE_WARNING_THRESHOLD and not errors:
        warnings.append(
            f"SEO title exceeds {SEO_TITLE_WARNING_THRESHOLD} characters ({len(seo_title)} chars). "
            f"This may be truncated in search results."
        )
    
    return errors, warnings


def _strength_in_text(text: str, strength_numeric: str, strength_unit: str) -> bool:
    """Check if strength token appears as a standalone unit in text.
    
    Uses regex for proper word boundary matching to avoid false positives
    (e.g., "2%" shouldn't match "GH20000").
    """
    num = strength_numeric
    unit = strength_unit.lower()
    
    # Pattern for "20mg" or "20 mg" with word boundaries
    pattern = rf"\b{re.escape(num)}\s*{re.escape(unit)}\b"
    if re.search(pattern, text, re.IGNORECASE):
        return True
    
    # Also check for percentage/mg equivalents
    if unit == "mg":
        pct_equiv = str(float(num) / 10)
        if pct_equiv.endswith(".0"):
            pct_equiv = pct_equiv[:-2]
        pct_pattern = rf"\b{re.escape(pct_equiv)}\s*%"
        if re.search(pct_pattern, text, re.IGNORECASE):
            return True
    elif unit == "%":
        mg_equiv = str(int(float(num) * 10))
        mg_pattern = rf"\b{re.escape(mg_equiv)}\s*mg\b"
        if re.search(mg_pattern, text, re.IGNORECASE):
            return True
    
    return False


def check_meta_description_tokens(
    meta_description: str,
    req: RequiredTokens,
) -> tuple[list[str], list[str]]:
    """Validate meta description contains required tokens.
    
    - Missing flavour is an error
    - Missing strength is a warning (not an error for meta)
    
    Matching: case-insensitive, &≡and, whitespace-collapsed. Flavour must appear as contiguous phrase.
    
    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []
    
    meta_norm = _normalize_for_comparison(meta_description)
    
    # Check flavour (required)
    if req.flavour:
        flavour_norm = _normalize_for_comparison(req.flavour)
        if flavour_norm not in meta_norm:
            errors.append(
                f"Meta description must contain the full flavour name '{req.flavour}'."
            )
    
    # Check strength (warning only for meta) - use regex for accurate matching
    if req.strength and req.strength_numeric and req.strength_unit:
        strength_found = _strength_in_text(
            meta_description, req.strength_numeric, req.strength_unit
        )
        
        if not strength_found:
            warnings.append(
                f"Meta description should include the nicotine strength '{req.strength}' for completeness."
            )
    
    return errors, warnings


def check_product_name_tokens(
    field: str,
    value: str,
    req: RequiredTokens,
    product_name: str | None = None,
    conn=None,
) -> tuple[list[str], list[str]]:
    """Check required tokens for a product SEO field.
    
    For seo_title: validates exact format '<product name> | Vapely Canada'
    For seo_description: validates flavour (error) and strength (warning)
    
    Returns (errors, warnings).
    """
    if field == "seo_title" and product_name:
        return check_seo_title_format(value, product_name, conn)
    elif field == "seo_description":
        return check_meta_description_tokens(value, req)
    
    return [], []
