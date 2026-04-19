"""SEO-optimised filename helpers for blog article images."""

import secrets

from ...seo_slug import slugify_article_handle

_SHORT_FILE_ID_ALPHABET = "23456789abcdefghjkmnpqrtvwxyz"


def _random_blog_file_suffix(length: int = 4) -> str:
    """Short disambiguator for filenames (avoids ambiguous 0/O/1/l)."""
    return "".join(secrets.choice(_SHORT_FILE_ID_ALPHABET) for _ in range(length))


def _blog_image_slug_stem(headline: str, topic: str, *, max_len: int = 52) -> str:
    """Compact SEO stem: comma-separated topic keywords when the title is long; else a trimmed title slug."""
    h = (headline or "").strip()
    t = (topic or "").strip()
    if not h and not t:
        return "blog"

    kws: list[str] = []
    seen: set[str] = set()
    for part in t.split(","):
        seg = slugify_article_handle(part.strip(), max_len=30)
        if len(seg) >= 3 and seg not in seen:
            seen.add(seg)
            kws.append(seg)

    headline_slug = slugify_article_handle(h or t, max_len=120)
    long_title = len(headline_slug) > 40 or len(h.split()) >= 7

    if kws and (long_title or not h):
        stem = "-".join(kws[:3])
    else:
        stem = headline_slug
        if len(stem) > max_len:
            cut = stem[:max_len]
            stem = cut.rsplit("-", 1)[0] if "-" in cut else cut.rstrip("-")

    stem = slugify_article_handle(stem.replace("--", "-"), max_len=max_len)
    if len(stem) > max_len:
        stem = stem[:max_len].rstrip("-")
        if "-" in stem:
            stem = stem.rsplit("-", 1)[0]
    return stem or "blog"


def _seo_blog_asset_filename(*, alt_text: str, headline: str = "", topic: str = "", ext: str) -> str:
    """SEO-optimised filename derived from the vision-generated alt text.

    The alt text describes the actual image pixels, so using it as the filename
    maximises alignment between filename, ``alt`` attribute, and visual content
    — the strongest signal combination for Google Image Search.

    Falls back to headline/topic stem when alt text is empty or too short.
    A 4-char random suffix prevents collisions across regenerations.
    """
    suffix = _random_blog_file_suffix(4)
    ext = ext if ext.startswith(".") else f".{ext}"
    max_stem = 50  # Google gives diminishing weight past ~50 chars

    alt = (alt_text or "").strip()
    # Slugify with generous limit, then trim at word boundary ourselves
    stem = slugify_article_handle(alt, max_len=120) if alt else ""

    # Fall back to headline/topic if alt is missing or too short to be useful
    if len(stem) < 8:
        stem = _blog_image_slug_stem(headline, topic, max_len=max_stem)

    # Trim at a word (hyphen) boundary so filenames never end mid-word.
    # Avoid leaving a stub like "e" from "e-liquid" by requiring 2+ chars in the last segment.
    if len(stem) > max_stem:
        cut = stem[:max_stem]
        while "-" in cut:
            cut = cut.rsplit("-", 1)[0]
            if not cut.endswith("-") and len(cut.rsplit("-", 1)[-1]) >= 2:
                break
        stem = cut

    return f"{stem}-{suffix}{ext}"
