"""Alt text sanitization, fallbacks, and structured builders."""

import re

_ALT_CAPTION_INSTRUCTION = (
    "Write concise image alt text for accessibility and SEO. Describe only what is clearly visible in the photo: "
    "main subjects, setting, lighting, and important objects. Do not invent brand names or on-image text unless "
    "you can clearly read them. No promotional or sales language. One short sentence, maximum 125 characters. "
    "Output only the alt text — no quotes, no label like 'Alt:'."
)


def _sanitize_image_alt(raw: str, *, max_len: int = 125) -> str:
    t = (raw or "").strip()
    t = t.strip("\"'""")
    t = re.sub(r"^(alt\s*text|description|image)\s*:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_len:
        cut = t[: max_len - 1]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        t = cut + "…"
    return t[:max_len]


def _fallback_alt_from_image_prompt(generation_prompt: str, *, max_len: int = 125) -> str:
    """If vision caption fails: derive alt from the same brief used to generate the image (scene intent, not title spam)."""
    p = re.sub(r"\s+", " ", (generation_prompt or "").strip())
    # Strip boilerplate prefixes used in image generation prompts
    _prefix_patterns = [
        r"^Wide blog cover photograph for a \w[\w\s]* online (?:e-commerce|vape) retail article\.\s*",
        r"^Close, in-article editorial photograph for the opening section only[^.]*\.\s*",
        r"^In-article editorial photograph for the section titled '[^']*'\.\s*",
    ]
    for pat in _prefix_patterns:
        p = re.sub(pat, "", p, flags=re.IGNORECASE)
    # Strip common boilerplate sentences that appear in all prompts
    _boilerplate = [
        r"\bNo text, logos, or watermarks\.\s*",
        r"\bPhotorealistic,[^.]*\.\s*",
        r"\b\w[\w\s]* online e-commerce retail context\.\s*",
        r"\bArticle title[^.]*\.\s*",
        r"\bMust visually illustrate[^.]*\.\s*",
        r"\bDistinct composition[^.]*\.\s*",
        r"\bContext from the section:\s*",
        r"\bTitle for reference:[^.]*\.\s*",
        r"\bGround the scene in what the first paragraph actually says[^.]*:\s*",
        r"\bMust read as a distinct illustration[^.]*\.\s*",
        # Featured cover prompt boilerplate
        r"\bTopic:\s*",
        r"\bTitle:\s*",
        r"\bPremium editorial hero[^.]*\.\s*",
        r"\b[Oo]pen composition[^.]*\.\s*",
        r"\b[Cc]ommercial quality\.\s*",
    ]
    for pat in _boilerplate:
        p = re.sub(pat, "", p, flags=re.IGNORECASE)
    p = p.strip()
    if not p:
        return "Photograph supporting an e-commerce retail blog article."[:max_len]
    if len(p) > max_len:
        cut = p[: max_len - 1].rsplit(" ", 1)[0]
        p = (cut + "…") if cut else p[:max_len]
    return p[:max_len]


def alt_text_from_prompt(generation_prompt: str) -> str:
    """Derive concise, SEO-friendly alt text directly from the image generation prompt.

    Since AI image generators faithfully reproduce their prompts, the prompt itself
    is the most reliable description of the image content.  This avoids an extra
    vision-model API call per image (which was the #1 source of alt-text failures).
    """
    return _fallback_alt_from_image_prompt(generation_prompt)


def _build_section_alt(heading: str, headline: str) -> str:
    """Build a concise, descriptive alt text for a section image from structured data.

    Uses the H2 heading as the primary source — it's topical, concise, and describes
    what the section (and therefore the image) is about.  Falls back to the article
    headline if the heading is too short.

    Examples:
        heading="Choosing the Right Pod System" → "Choosing the right pod system for vaping"
        heading="Safety Standards" → "Safety standards in e-liquid manufacturing"
    """
    h = (heading or "").strip()
    t = (headline or "").strip()

    if h:
        # Clean the heading: remove trailing punctuation, normalise case
        alt = re.sub(r"[:.!?]+$", "", h).strip()
        # If the heading is very short (< 4 words), append context from headline
        if len(alt.split()) < 4 and t:
            # Extract a few meaningful words from headline to add context
            title_words = re.sub(r"[^a-zA-Z0-9\s]", " ", t).split()
            extra = [w for w in title_words if w.lower() not in alt.lower().split()][:3]
            if extra:
                alt = f"{alt} — {' '.join(extra).lower()}"
        return alt[:125]

    if t:
        return re.sub(r"[:.!?]+$", "", t).strip()[:125]

    return "Photograph supporting an e-commerce retail blog article"


def _build_featured_alt(headline: str, topic: str) -> str:
    """Build a concise alt text for the featured / hero cover image.

    Uses the article headline directly — it's the best summary of what the
    entire article (and therefore the hero image) is about.
    """
    h = (headline or "").strip()
    t = (topic or "").strip()

    if h:
        return re.sub(r"[:.!?]+$", "", h).strip()[:125]
    if t:
        # Topic is often comma-separated keywords; join first few
        parts = [p.strip() for p in t.split(",") if p.strip()][:4]
        return ", ".join(parts)[:125]

    return "Featured image for an e-commerce retail blog article"
