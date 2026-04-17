"""URL-safe handles for blog articles (Shopify `handle` field)."""

from __future__ import annotations

import re
import unicodedata


def slugify_article_handle(text: str, *, max_len: int = 96) -> str:
    """Turn a title or topic into a Shopify-style handle: lowercase, hyphens, a-z0-9 only.

    Strips accents, collapses punctuation to single hyphens, trims length for readable URLs.
    """
    raw = (text or "").strip()
    if not raw:
        return "article"

    normalized = unicodedata.normalize("NFKD", raw)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lower = ascii_only.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lower)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")

    if not slug:
        return "article"

    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")

    return slug or "article"


# Words that add no ranking value in a URL slug.
_SLUG_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "about", "from", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "under", "over", "out", "up", "down", "off", "then", "than", "so",
    "no", "not", "only", "very", "just", "how", "what", "when", "where",
    "which", "who", "whom", "why", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "own", "same", "too", "also",
    "your", "you", "its", "our", "their", "my", "this", "that", "these",
    "those", "here", "there", "again", "once", "i", "we", "he", "she",
    "it", "they", "me", "him", "her", "us", "them", "need", "know",
    "everything", "nothing", "something", "anything", "guide", "complete",
    "ultimate", "best", "top", "rated",
}


def seo_article_slug(
    title: str,
    *,
    keywords: list[str] | None = None,
    max_words: int = 5,
    max_len: int = 60,
) -> str:
    """Build a concise, keyword-rich URL slug from the article title and optional keywords.

    Strategy:
    1. Extract meaningful words from the title (strip stop words + filler).
    2. Append any keyword terms not already present.
    3. Target *max_words* words — enough for search intent, short enough for clean URLs.

    Examples:
        title="Everything You Need to Know About SMOK Novo Pod Systems"
        keywords=["novo", "pod", "coils"]
        → "smok-novo-pod-systems-coils"
    """
    raw = (title or "").strip()
    if not raw:
        return slugify_article_handle("article")

    # Normalise to ASCII lowercase words
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    words = re.sub(r"[^a-z0-9\s-]", " ", ascii_only).split()
    meaningful = [w for w in words if len(w) >= 2 and w not in _SLUG_STOP_WORDS]

    seen: set[str] = set()
    parts: list[str] = []
    for w in meaningful:
        if w not in seen and len(parts) < max_words:
            seen.add(w)
            parts.append(w)

    # Fill remaining slots with keyword terms not already in the title
    for kw_phrase in keywords or []:
        for w in re.sub(r"[^a-z0-9\s]", " ", kw_phrase.lower()).split():
            if len(w) >= 2 and w not in seen and len(parts) < max_words:
                seen.add(w)
                parts.append(w)

    if not parts:
        return slugify_article_handle(raw, max_len=max_len)

    slug = "-".join(parts)
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
        if "-" in slug:
            slug = slug.rsplit("-", 1)[0]

    return slug or slugify_article_handle(raw, max_len=max_len)
