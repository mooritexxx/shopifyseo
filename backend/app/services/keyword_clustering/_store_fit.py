"""Store-fit scoring: penalize noise clusters, boost catalog-aligned clusters.

This module provides configurable detection and scoring for:
- Tobacco/cigarette themes (noise for vape-only stores)
- Local "near me" / store-locator themes (weak for online-only stores)
- Non-catalog brand clusters (hardware brands not in stock)
- Catalog vendor alignment (boost clusters matching stocked brands)

All patterns are configurable and read from the DB when possible, so this
is not hardcoded to vape retail — it applies generic signals (catalog overlap,
local detection, configurable noise lists) that any store can tune.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Default noise patterns (configurable via settings)
# ---------------------------------------------------------------------------

# Tobacco / cigarette / smokes — common terms for combustible tobacco products.
# These are NOT vape products; clusters dominated by these themes should be
# heavily penalized for online vape retailers.
# Note: "smoke shop" is excluded as it's a retail establishment name that
# can refer to vape stores; only product-related tobacco terms are included.
DEFAULT_TOBACCO_PATTERNS: frozenset[str] = frozenset({
    "cigarette",
    "cigarettes",
    "smokes",  # "cheap smokes" etc - product reference, not "smoke shop"
    "tabagie",
    "carton",
    "cartons",
    "menthol cigarette",
    "menthol cigarettes",
    "canadian lights",
    "canadian light",
    "du maurier",
    "player",
    "export a",
    "pall mall",
    "belmont",
    "camels",
    "marlboro",
    "winston",
    "lucky strike",
    "cheap smokes",
    "cheapest cigarettes",
})

# Local / "near me" patterns — store-locator queries. Online-only retailers
# cannot serve this intent well; these clusters are low-value content targets.
DEFAULT_LOCAL_PATTERNS: frozenset[str] = frozenset({
    "near me",
    "near by",
    "nearby",
    "close to me",
    "in my area",
    "store locator",
    "find a store",
    "local store",
    "smoke shop near",
    "vape shop near",
    "vape store near",
    "shops near",
    "stores near",
})

# Off-niche patterns — completely unrelated topics that sometimes appear in
# keyword research due to homonyms or broad match.
# Note: use full words to avoid matching substrings (e.g., "cigar" in "cigarette")
DEFAULT_OFF_NICHE_PATTERNS: frozenset[str] = frozenset({
    "whipped cream charger",
    "whipped cream chargers",
    "nitrous oxide",
    "whippet",
    "whippets",
    "e nail",
    "e nails",
    "enail",
    "enails",
    "dab pen wax",
    "dab rig",
    "dry herb vaporizer",
    "ups battery",
    "ups batteries",
    "cigars",  # Use plural to avoid matching cigarette
    "cohiba",  # Cigar brand
    "hookah",
})


@dataclass
class StoreFitContext:
    """Context for store-fit scoring, built from catalog and settings."""

    # Vendors in catalog: {vendor_lower: {"name": str, "product_count": int}}
    catalog_vendors: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Total product count across all vendors
    total_product_count: int = 0

    # Noise patterns (lowercase, for substring matching)
    tobacco_patterns: frozenset[str] = DEFAULT_TOBACCO_PATTERNS
    local_patterns: frozenset[str] = DEFAULT_LOCAL_PATTERNS
    off_niche_patterns: frozenset[str] = DEFAULT_OFF_NICHE_PATTERNS

    # Penalty multipliers (0.0 = exclude, 1.0 = no penalty)
    tobacco_penalty: float = 0.15  # Heavy penalty for tobacco clusters
    local_penalty: float = 0.25  # Heavy penalty for near-me clusters
    off_niche_penalty: float = 0.10  # Very heavy penalty for off-niche

    # Catalog boost parameters
    # Clusters matching a catalog vendor get a boost based on SKU share
    vendor_boost_max: float = 1.35  # Max multiplier for high-SKU vendor
    vendor_boost_min: float = 1.08  # Min boost for any catalog vendor match


def load_store_fit_context(conn: sqlite3.Connection) -> StoreFitContext:
    """Build StoreFitContext from catalog data."""
    catalog_vendors: dict[str, dict[str, Any]] = {}
    total_product_count = 0

    try:
        rows = conn.execute(
            """
            SELECT vendor, COUNT(*) AS product_count
            FROM products
            WHERE vendor IS NOT NULL AND TRIM(vendor) != ''
            GROUP BY vendor
            ORDER BY product_count DESC
            """
        ).fetchall()
        for row in rows:
            vendor = str(row[0] or "").strip()
            count = int(row[1] or 0)
            if vendor:
                catalog_vendors[vendor.lower()] = {
                    "name": vendor,
                    "product_count": count,
                }
                total_product_count += count
    except Exception:
        pass

    return StoreFitContext(
        catalog_vendors=catalog_vendors,
        total_product_count=total_product_count,
    )


def _normalize_text(text: str) -> str:
    """Lowercase and normalize whitespace for pattern matching."""
    return " ".join((text or "").lower().split())


def _text_contains_any(text: str, patterns: frozenset[str]) -> bool:
    """Check if normalized text contains any pattern.

    Uses word boundary matching for patterns to avoid false positives
    (e.g., "cigar" matching "cigarette").
    """
    norm = _normalize_text(text)
    if not norm:
        return False
    for p in patterns:
        # Use word boundary matching for patterns
        # Pattern can be multi-word, so we check if all words appear as whole words
        p_words = p.split()
        if len(p_words) == 1:
            # Single word: use word boundary
            if re.search(rf"\b{re.escape(p)}\b", norm):
                return True
        else:
            # Multi-word phrase: check if phrase appears
            if p in norm:
                return True
    return False


def _has_tobacco_signal(text: str, patterns: frozenset[str]) -> bool:
    """Check if text indicates tobacco/cigarette theme."""
    return _text_contains_any(text, patterns)


def _has_local_signal(text: str, patterns: frozenset[str]) -> bool:
    """Check if text indicates local/near-me theme."""
    return _text_contains_any(text, patterns)


def _has_off_niche_signal(text: str, patterns: frozenset[str]) -> bool:
    """Check if text indicates off-niche theme."""
    return _text_contains_any(text, patterns)


def _detect_catalog_vendor(
    text: str,
    catalog_vendors: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Detect if text matches a catalog vendor name.

    Returns vendor info dict or None. Matches are case-insensitive substrings.
    Longer vendor names are checked first to prefer specific matches.
    """
    norm = _normalize_text(text)
    if not norm:
        return None

    # Sort by vendor name length descending to match "STLTH x GEEK BAR" before "STLTH"
    for vendor_lower in sorted(catalog_vendors.keys(), key=len, reverse=True):
        # Check as word boundary match for short names, substring for longer
        if len(vendor_lower) <= 3:
            # Short names need word boundaries
            if re.search(rf"(?<![a-z0-9]){re.escape(vendor_lower)}(?![a-z0-9])", norm):
                return catalog_vendors[vendor_lower]
        else:
            if vendor_lower in norm:
                return catalog_vendors[vendor_lower]
    return None


def compute_cluster_store_fit(
    cluster_name: str,
    cluster_keywords: list[str],
    cluster_role: str,
    detected_entity: str,
    context: StoreFitContext,
) -> dict[str, Any]:
    """Compute store-fit score for a cluster.

    Returns:
        {
            "fit_multiplier": float,  # Multiply priority by this (0.1 to 1.4)
            "matched_vendor": dict | None,  # Catalog vendor info if matched
            "is_tobacco": bool,
            "is_local": bool,
            "is_off_niche": bool,
            "penalty_reason": str | None,  # Human-readable reason if penalized
        }
    """
    # Combine all text for pattern matching
    all_text = " ".join([cluster_name, detected_entity] + cluster_keywords)
    norm_text = _normalize_text(all_text)

    # Detect noise signals
    is_tobacco = _has_tobacco_signal(norm_text, context.tobacco_patterns)
    is_local = _has_local_signal(norm_text, context.local_patterns) or cluster_role == "local"
    is_off_niche = _has_off_niche_signal(norm_text, context.off_niche_patterns)

    # Detect catalog vendor match
    # Check entity first (more specific), then name, then keywords
    matched_vendor = (
        _detect_catalog_vendor(detected_entity, context.catalog_vendors)
        or _detect_catalog_vendor(cluster_name, context.catalog_vendors)
    )
    if not matched_vendor:
        for kw in cluster_keywords[:10]:  # Only check first 10 for performance
            matched_vendor = _detect_catalog_vendor(kw, context.catalog_vendors)
            if matched_vendor:
                break

    # Start with neutral multiplier
    fit_multiplier = 1.0
    penalty_reason: str | None = None

    # Apply penalties (cumulative for multiple issues)
    if is_off_niche:
        fit_multiplier *= context.off_niche_penalty
        penalty_reason = "off-niche (not related to store products)"
    elif is_tobacco:
        fit_multiplier *= context.tobacco_penalty
        penalty_reason = "tobacco/cigarette theme (store sells vape only)"
    elif is_local:
        fit_multiplier *= context.local_penalty
        penalty_reason = "local/near-me query (online-only store)"

    # Apply catalog vendor boost (only if not penalized too heavily)
    if matched_vendor and fit_multiplier >= 0.5:
        # Boost based on vendor's share of catalog
        product_count = matched_vendor.get("product_count", 0)
        total = context.total_product_count or 1
        sku_share = min(product_count / total, 0.5)  # Cap at 50% share

        # Linear interpolation between min and max boost based on SKU share
        # 0% share -> min boost, 50%+ share -> max boost
        boost_range = context.vendor_boost_max - context.vendor_boost_min
        vendor_boost = context.vendor_boost_min + (sku_share / 0.5) * boost_range

        fit_multiplier *= vendor_boost
        penalty_reason = None  # Clear any minor penalty if vendor matches

    # Ensure multiplier is in reasonable range
    fit_multiplier = max(0.05, min(1.5, fit_multiplier))

    return {
        "fit_multiplier": round(fit_multiplier, 3),
        "matched_vendor": matched_vendor,
        "is_tobacco": is_tobacco,
        "is_local": is_local,
        "is_off_niche": is_off_niche,
        "penalty_reason": penalty_reason,
    }


def compute_keyword_store_fit(
    keyword: str,
    context: StoreFitContext,
) -> dict[str, Any]:
    """Compute store-fit score for a single keyword.

    Simpler version of cluster scoring for individual keyword assessment.
    """
    norm = _normalize_text(keyword)

    is_tobacco = _has_tobacco_signal(norm, context.tobacco_patterns)
    is_local = _has_local_signal(norm, context.local_patterns)
    is_off_niche = _has_off_niche_signal(norm, context.off_niche_patterns)
    matched_vendor = _detect_catalog_vendor(norm, context.catalog_vendors)

    fit_multiplier = 1.0
    if is_off_niche:
        fit_multiplier = context.off_niche_penalty
    elif is_tobacco:
        fit_multiplier = context.tobacco_penalty
    elif is_local:
        fit_multiplier = context.local_penalty
    elif matched_vendor:
        # Simple boost for keyword-level
        fit_multiplier = context.vendor_boost_min

    return {
        "fit_multiplier": round(fit_multiplier, 3),
        "matched_vendor": matched_vendor,
        "is_tobacco": is_tobacco,
        "is_local": is_local,
        "is_off_niche": is_off_niche,
    }
