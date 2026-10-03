"""Content and anchor compliance checks for manual-weave link insertions.

This module provides guards G8–G11 as defined in the manual-weave design:
- G8: Descriptive anchor, no exact-match keyword stuffing
- G9: Numbers outside anchor text
- G10: Banned wording (health claims, denylist categories)
- G11: Stock/availability claims, commerce heading check, TVPA flavour check
"""
from __future__ import annotations

import re
from typing import Sequence

from ..dashboard_ai_engine_parts.faq_content_filter import (
    health_claim_reason,
    _match_denylist_category,
)
from ..dashboard_ai_engine_parts.commerce_heading_gate import classify_commerce_heading
from .anchors import is_weak_anchor

# TVPA flavour module from PR #45 - import when available
try:
    from ..dashboard_ai_engine_parts.tvpa_flavour import (
        tvpa_flavour_matches,
        extract_flavour_from_title,
    )
    _TVPA_AVAILABLE = True
except ImportError:
    tvpa_flavour_matches = None  # type: ignore[assignment,misc]
    extract_flavour_from_title = None  # type: ignore[assignment,misc]
    _TVPA_AVAILABLE = False

__all__ = [
    "manual_weave_gaps",
    "check_anchor_quality",
    "check_numbers_outside_anchor",
    "check_stock_availability_claims",
    "TVPA_AVAILABLE",
]

TVPA_AVAILABLE = _TVPA_AVAILABLE

# Supplementary stock/availability patterns not covered by commerce_heading_gate
# (these are addition-level checks, not heading-level)
_SUPPLEMENTARY_STOCK_PATTERNS = [
    re.compile(r"\bships?\s+(?:today|same[- ]day|tomorrow|within)\b", re.IGNORECASE),
    re.compile(r"\bsame[- ]day\s+(?:shipping|delivery)\b", re.IGNORECASE),
    re.compile(r"\b(?:limited|low)\s+stock\b", re.IGNORECASE),
    re.compile(r"\bonly\s+\d+\s+left\b", re.IGNORECASE),
    re.compile(r"\bwhile\s+supplies\s+last\b", re.IGNORECASE),
    re.compile(r"\bselling\s+fast\b", re.IGNORECASE),
    re.compile(r"\bback\s+in\s+stock\b", re.IGNORECASE),
]

# Common English stop words to ignore in repeated-word check
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must", "shall", "can",
    "this", "that", "these", "those", "it", "its", "my", "your", "our",
    "their", "his", "her", "we", "you", "they", "i", "me", "him", "us",
})

# Digit-bearing token pattern
_DIGIT_TOKEN_RE = re.compile(r"\b\S*\d\S*\b")


def check_anchor_quality(
    anchor: str,
    *,
    target_title: str = "",
    target_keywords: Sequence[str] = (),
) -> list[str]:
    """Check anchor text quality (G8).
    
    Returns a list of gap messages if the anchor fails any check:
    - (a) 1-120 characters (checked by validate_edit, not here)
    - (b) is_weak_anchor rejects single generic words
    - (c) Exact-match keyword stuffing: anchor equals a target keyword that
          is NOT contained in the target's title
    - (d) Repeated words in the anchor (ignoring case and stop words)
    """
    gaps: list[str] = []
    
    if not anchor or not anchor.strip():
        gaps.append("Anchor text is empty.")
        return gaps
    
    anchor_clean = anchor.strip()
    
    # (b) Weak anchor check
    if is_weak_anchor(anchor_clean):
        gaps.append(f'Anchor "{anchor_clean}" is a generic single word; use a more descriptive phrase.')
    
    # (c) Exact-match keyword stuffing
    anchor_lower = anchor_clean.lower()
    title_lower = (target_title or "").lower()
    for kw in target_keywords:
        kw_lower = (kw or "").lower().strip()
        if not kw_lower:
            continue
        if anchor_lower == kw_lower:
            # It's an exact match; check if the keyword appears in the title
            if kw_lower not in title_lower:
                gaps.append(
                    f'Anchor "{anchor_clean}" is an exact keyword match but not in the target title; '
                    f"rephrase to be more descriptive."
                )
                break
    
    # (d) Repeated words
    words = anchor_lower.split()
    seen: set[str] = set()
    for w in words:
        w_clean = re.sub(r"[^\w]", "", w)
        if not w_clean or w_clean in _STOP_WORDS:
            continue
        if w_clean in seen:
            gaps.append(f'Anchor "{anchor_clean}" contains repeated word "{w_clean}"; remove duplication.')
            break
        seen.add(w_clean)
    
    return gaps


def check_numbers_outside_anchor(addition: str, anchor: str) -> list[str]:
    """Check for digit-bearing tokens outside the anchor text (G9).
    
    Numbers inside the anchor are allowed (e.g. "STLTH 60K buying guide").
    Numbers outside the anchor are rejected (e.g. "5000 puffs", "20mg").
    """
    gaps: list[str] = []
    
    if not addition:
        return gaps
    
    # Find where the anchor appears in the addition
    anchor_lower = (anchor or "").lower()
    addition_lower = addition.lower()
    
    # Find anchor position
    anchor_start = addition_lower.find(anchor_lower) if anchor_lower else -1
    anchor_end = anchor_start + len(anchor) if anchor_start >= 0 else -1
    
    # Find all digit-bearing tokens
    for match in _DIGIT_TOKEN_RE.finditer(addition):
        token = match.group()
        start, end = match.start(), match.end()
        
        # Check if this token is inside the anchor
        if anchor_start >= 0 and start >= anchor_start and end <= anchor_end:
            continue
        
        gaps.append(
            f'Number "{token}" in the addition is outside the anchor text; '
            f"numbers are only allowed inside the link anchor."
        )
        break  # One gap is enough
    
    return gaps


def check_stock_availability_claims(text: str) -> list[str]:
    """Check for stock/availability claims in text (G11 supplement).
    
    Combines:
    - classify_commerce_heading() for in-stock, available now, sold out, restock, etc.
    - Supplementary patterns: ships today, limited stock, only N left, etc.
    """
    gaps: list[str] = []
    
    if not text:
        return gaps
    
    # Check via commerce_heading_gate
    classification = classify_commerce_heading(text)
    if classification == "stock_status":
        gaps.append("Text claims stock status or availability (e.g. 'in stock', 'available now').")
    elif classification == "bulk_wholesale":
        gaps.append("Text mentions bulk or wholesale; the store is retail-only.")
    
    # Check supplementary patterns
    for pattern in _SUPPLEMENTARY_STOCK_PATTERNS:
        match = pattern.search(text)
        if match:
            matched_text = match.group()
            gaps.append(f'Stock/availability claim "{matched_text}" is not allowed.')
            break
    
    return gaps


def manual_weave_gaps(
    addition: str,
    anchor: str,
    *,
    allowed_names: Sequence[str] = (),
    target_title: str = "",
    target_keywords: Sequence[str] = (),
) -> list[str]:
    """Check all content compliance rules for a manual-weave addition.
    
    Combines guards G8-G11:
    - G8: Anchor quality (weak anchors, exact-match stuffing, repeated words)
    - G9: Numbers outside anchor
    - G10: Banned wording (health claims, denylist categories)
    - G11: Stock/availability claims + commerce heading + TVPA flavour
    
    Parameters:
        addition: The plain text being appended (without the link markup).
        anchor: The anchor text for the link.
        allowed_names: TVPA allowlist - product/flavour names that should not trigger
                       TVPA warnings (source and target titles + extracted flavours).
        target_title: The target page/product title for anchor stuffing check.
        target_keywords: Keywords associated with the target for anchor stuffing check.
    
    Returns:
        A list of gap messages. Empty list means the content passes all checks.
    """
    gaps: list[str] = []
    
    # G8: Anchor quality
    gaps.extend(check_anchor_quality(
        anchor,
        target_title=target_title,
        target_keywords=target_keywords,
    ))
    
    # G9: Numbers outside anchor
    gaps.extend(check_numbers_outside_anchor(addition, anchor))
    
    # G10: Banned wording - health claims
    health_reason = health_claim_reason(addition)
    if health_reason:
        gaps.append(f"Health claim detected: {health_reason}. Rephrase to remove health/safety language.")
    
    # G10: Banned wording - denylist categories
    denylist_match = _match_denylist_category(addition)
    if denylist_match:
        category_name, description = denylist_match
        gaps.append(f"Banned content ({category_name}): {description}")
    
    # G11: Stock/availability claims
    gaps.extend(check_stock_availability_claims(addition))
    
    # G11: TVPA flavour check (when available)
    if _TVPA_AVAILABLE and tvpa_flavour_matches is not None:
        allowed_set = tuple(allowed_names) if allowed_names else ()
        matches = tvpa_flavour_matches(addition, allowed_names=allowed_set)
        if matches:
            for m in matches:
                group = m.get("group", "unknown")
                term = m.get("term", "")
                if group == "category":
                    gaps.append(f'TVPA violation: "{term}" is a restricted flavour category term.')
                elif group == "style":
                    gaps.append(f'TVPA violation: "{term}" is a restricted flavour style descriptor.')
                else:
                    gaps.append(f'TVPA violation: "{term}" triggers flavour comparison rules.')
    
    return gaps


def build_tvpa_allowlist(
    source_title: str = "",
    target_title: str = "",
) -> tuple[str, ...]:
    """Build the TVPA allowlist from source and target titles.
    
    When tvpa_flavour.py is available, extracts flavour names from titles
    and combines them with the titles themselves.
    """
    names: list[str] = []
    
    if source_title:
        names.append(source_title)
    if target_title:
        names.append(target_title)
    
    if _TVPA_AVAILABLE and extract_flavour_from_title is not None:
        if source_title:
            extracted = extract_flavour_from_title(source_title)
            if extracted:
                names.append(extracted)
        if target_title:
            extracted = extract_flavour_from_title(target_title)
            if extracted:
                names.append(extracted)
    
    return tuple(names)
