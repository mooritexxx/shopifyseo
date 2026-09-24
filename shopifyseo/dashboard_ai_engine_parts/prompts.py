import json

from .config import BODY_MIN_LENGTH, DESCRIPTION_HARD_MIN, DESCRIPTION_LIMIT, DESCRIPTION_TARGET_MAX, DESCRIPTION_TARGET_MIN, TITLE_HARD_MIN, TITLE_LIMIT, TITLE_TARGET_MIN
from .context import _slim_keyword_context, condensed_context, curated_primary_object, infer_product_intent, json_list, prompt_context, signal_availability_summary, strip_html, word_count

_GSC_QUERY_HIGHLIGHTS_MAX = 3


def build_description_length_retry_feedback(
    previous_draft: str,
    target_min: int = 150,
    target_max: int = 160,
    object_type: str = "product",
) -> str:
    """Build explicit retry instructions when seo_description is too short.
    
    Returns a string to be injected into the user prompt as <retry_instruction>.
    Shows the previous draft and its length, with explicit rewrite guidance.
    """
    prev_len = len(previous_draft)
    
    # Build context-specific expansion hints based on object type
    if object_type == "product":
        expansion_hint = (
            "Add concrete product details: brand positioning, key spec (puff count, nicotine, flavour profile), "
            "or Canada-market benefit (fast shipping, local stock). "
        )
    elif object_type == "collection":
        expansion_hint = (
            "Add collection value: product variety, brand coverage, or Canada-market relevance. "
        )
    else:
        expansion_hint = (
            "Add useful detail: page purpose, key benefit, or Canada-market relevance. "
        )
    
    return (
        f"RETRY REQUIRED — PREVIOUS DRAFT TOO SHORT\n"
        f"Previous draft ({prev_len} characters):\n"
        f'"{previous_draft}"\n\n'
        f"This is {target_min - prev_len} characters below the minimum target.\n"
        f"REWRITE to exactly {target_min}–{target_max} characters (count every character including spaces).\n"
        f"{expansion_hint}"
        f"Do NOT pad with fluff, filler, or repetition. Do NOT exceed {target_max} characters."
    )


def build_description_length_repair_prompt(
    draft: str,
    target_min: int,
    target_max: int,
    expansion_bits: list[str],
) -> tuple[str, str]:
    """Build a focused LLM repair prompt for expanding a too-short seo_description.
    
    Returns (system_prompt, user_prompt) for a dedicated repair call.
    The repair should KEEP the meaning and EXPAND by adding concrete detail.
    """
    draft_len = len(draft)
    shortfall = target_min - draft_len
    
    # Filter expansion bits to only those not already in the draft (case-insensitive)
    draft_lower = draft.lower()
    available_bits = [b for b in expansion_bits if b.lower() not in draft_lower]
    
    system_prompt = (
        "You are a copy editor. Your ONLY task is to expand a too-short SEO meta description "
        "to exactly 150–160 characters. You must KEEP the original meaning and wording as much as possible. "
        "Add detail from the provided expansion options. Do NOT invent facts. Do NOT shorten. "
        "Output ONLY the expanded description as plain text — no quotes, no JSON, no explanation."
    )
    
    bits_text = ", ".join(f'"{b}"' for b in available_bits[:6]) if available_bits else "(none available)"
    
    user_prompt = (
        f"CURRENT DRAFT ({draft_len} characters, need {shortfall} more):\n"
        f'"{draft}"\n\n'
        f"EXPANSION OPTIONS (factual details you may insert):\n{bits_text}\n\n"
        f"TASK: Expand to exactly {target_min}–{target_max} characters. "
        f"Insert detail naturally — mid-sentence or at the end. "
        f"Do NOT rewrite from scratch. Do NOT shorten. Do NOT exceed {target_max}. "
        f"Output ONLY the expanded text."
    )
    
    return system_prompt, user_prompt


def extract_expansion_bits_from_context(context: dict, object_type: str) -> list[str]:
    """Extract factual expansion bits from generation context for length repair.
    
    Returns a list of short phrases that can be inserted to expand a description.
    These are derived from product/collection/page context, not invented.
    """
    bits: list[str] = []
    detail = context.get("detail") or {}
    
    if object_type == "product":
        product = detail.get("product") or {}
        
        # Brand / vendor
        vendor = product.get("vendor", "").strip()
        if vendor and len(vendor) <= 25:
            bits.append(f"by {vendor}")
        
        # Puff count from title or tags
        title = product.get("title", "")
        import re
        puff_match = re.search(r'\b(\d{3,5})\s*(?:puff|Puff)', title)
        if puff_match:
            bits.append(f"{puff_match.group(1)} puffs")
        
        # K-notation puff count (e.g., "10K" = 10000 puffs)
        k_match = re.search(r'\b(\d+)[Kk]\b', title)
        if k_match:
            k_val = int(k_match.group(1)) * 1000
            bits.append(f"{k_val:,} puffs")
        
        # Product type
        ptype = product.get("product_type", "").strip()
        if ptype and len(ptype) <= 20:
            bits.append(ptype.lower())
        
        # Tags for flavour hints
        tags = product.get("tags", "")
        if isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            tag_list = list(tags) if tags else []
        for tag in tag_list[:5]:
            if len(tag) <= 15 and tag.lower() not in ("new", "sale", "featured"):
                bits.append(tag.lower())
        
        # Canada shipping bits
        bits.append("ships across Canada")
        bits.append("Canadian shipping available")
        bits.append("fast Canada-wide delivery")
        
    elif object_type == "collection":
        collection = detail.get("collection") or {}
        title = collection.get("title", "").strip()
        if title:
            bits.append(f"from our {title} collection")
        bits.append("wide selection")
        bits.append("shop the full range")
        bits.append("Canadian shoppers")
        bits.append("ships Canada-wide")
        
    else:  # page, blog_article
        page = detail.get("page") or detail.get("article") or {}
        title = page.get("title", "").strip()
        if title and len(title) <= 30:
            bits.append(title.lower())
        bits.append("learn more")
        bits.append("Canadian customers")
        bits.append("ships across Canada")
    
    # Dedupe while preserving order
    seen = set()
    unique_bits = []
    for b in bits:
        b_lower = b.lower()
        if b_lower not in seen and len(b) >= 3:
            seen.add(b_lower)
            unique_bits.append(b)
    
    return unique_bits


def ensure_seo_description_length(
    text: str,
    *,
    target_min: int = 150,
    target_max: int = 160,
    expansion_bits: list[str],
) -> tuple[str, bool]:
    """Deterministic fallback to ensure seo_description is in [target_min, target_max].
    
    Args:
        text: The description text to fix
        target_min: Minimum character count (default 150)
        target_max: Maximum character count (default 160)
        expansion_bits: List of factual phrases to append for expansion
    
    Returns:
        (fixed_text, was_modified): The fixed text and whether any change was made
    
    Rules:
        - If already in range → return as-is
        - If > target_max → return as-is (leave to existing trim/reject logic)
        - If < target_min → append expansion bits until in [target_min, target_max]
        - Skip bits already present in the text (case-insensitive)
        - Never exceed target_max
        - Prefer landing as close to target_max as possible
    """
    text = text.strip()
    current_len = len(text)
    
    # Already in range
    if target_min <= current_len <= target_max:
        return text, False
    
    # Too long — don't modify here (leave to existing logic)
    if current_len > target_max:
        return text, False
    
    # Too short — need to expand
    text_lower = text.lower()
    
    # Filter to bits not already in text
    available_bits = [b for b in expansion_bits if b.lower() not in text_lower]
    
    # Sort by length descending to fill space efficiently, then by length ascending for fine-tuning
    # Strategy: first try to get close with longer bits, then fine-tune with shorter ones
    available_bits_sorted = sorted(available_bits, key=len, reverse=True)
    
    result = text
    
    # Try to append bits to reach target range
    for bit in available_bits_sorted:
        current_len = len(result)
        
        # Already in target range — stop
        if target_min <= current_len <= target_max:
            break
        
        # Calculate what adding this bit would give us
        # Try different insertion formats
        candidates = [
            f"{result} {bit}.",  # Append with period
            f"{result}. {bit.capitalize()}.",  # New sentence
            f"{result} — {bit}.",  # Em-dash
        ]
        
        # If result ends with period, try replacing it
        if result.rstrip().endswith("."):
            base = result.rstrip()[:-1]
            candidates.extend([
                f"{base} — {bit}.",
                f"{base}; {bit}.",
            ])
        
        # Pick the best candidate that lands in or closest to target range without exceeding
        best_candidate = None
        best_dist = float('inf')
        
        for candidate in candidates:
            cand_len = len(candidate)
            if cand_len > target_max:
                continue  # Skip if would exceed max
            if target_min <= cand_len <= target_max:
                # In range — prefer closer to target_max
                dist = target_max - cand_len
                if dist < best_dist:
                    best_dist = dist
                    best_candidate = candidate
            elif cand_len < target_min and cand_len > current_len:
                # Still short but longer — might help
                dist = target_min - cand_len
                if dist < best_dist:
                    best_dist = dist
                    best_candidate = candidate
        
        if best_candidate and len(best_candidate) > current_len:
            result = best_candidate
    
    # Final check — if we got into range, great; otherwise return best effort
    was_modified = result != text
    return result, was_modified


_GSC_QUERY_HIGHLIGHTS_JSON_CAP = 400

# Verified few-shot examples for SEO description generation.
# IMPORTANT: These lengths are verified in unit tests — do not change without updating tests.
SEO_DESCRIPTION_EXAMPLE_1 = "Shop Draggg 10K Frost disposable vape online in Canada. Premium icy menthol flavour with 10,000 puffs and 20mL e-liquid. Fast Canada-wide shipping available."
SEO_DESCRIPTION_EXAMPLE_1_LEN = 157  # Verified: len(SEO_DESCRIPTION_EXAMPLE_1) == 157

SEO_DESCRIPTION_EXAMPLE_2 = "Discover the Elf Bar BC5000 Strawberry Banana at Vapely. Smooth fruity flavour, 5000 puffs, rechargeable. Ships across Canada with express delivery options."
SEO_DESCRIPTION_EXAMPLE_2_LEN = 156  # Verified: len(SEO_DESCRIPTION_EXAMPLE_2) == 156


# Body regeneration: steer internal links toward embedding-neighbor examples when allowlists are large.
_RAG_INTERNAL_LINK_PREFERENCE = (
    "When `related_content_examples` in <context> lists similar store content, prefer those destinations "
    "(same `type` and `handle` in `approved_internal_link_targets`) when they fit the narrative over unrelated "
    "URLs that appear only deeper in the allowlist. "
)


def _gsc_query_highlights_for_slim(full_context: dict) -> list[dict]:
    """Top few GSC queries for meta-description slim prompts (strict JSON size cap)."""
    rows: list[dict] = list(full_context.get("top_queries") or [])
    if not rows:
        sc = full_context.get("seo_context") or {}
        for r in sc.get("gsc_query_rows") or []:
            rows.append(
                {
                    "query": r.get("query"),
                    "impressions": r.get("impressions"),
                    "ctr": r.get("ctr"),
                    "position": r.get("position"),
                }
            )
    out: list[dict] = []
    for row in rows[:_GSC_QUERY_HIGHLIGHTS_MAX]:
        q = str(row.get("query") or "").strip()[:160]
        if not q:
            continue
        out.append(
            {
                "query": q,
                "impressions": int(row.get("impressions") or 0),
                "ctr": float(row.get("ctr") or 0),
                "position": float(row.get("position") or 0),
            }
        )
    while len(out) > 0 and len(json.dumps(out, ensure_ascii=True)) > _GSC_QUERY_HIGHLIGHTS_JSON_CAP:
        out.pop()
    return out


_STORE_DESCRIPTION_MAX_LEN = 500


def _load_store_description(conn) -> str:
    """Load store_description from settings, trimmed for prompt injection."""
    if conn is None:
        return ""
    try:
        from shopifyseo.dashboard_google import get_service_setting
        raw = (get_service_setting(conn, "store_description") or "").strip()
        if len(raw) > _STORE_DESCRIPTION_MAX_LEN:
            return raw[:_STORE_DESCRIPTION_MAX_LEN - 1] + "…"
        return raw
    except Exception:
        return ""


def _market_ctx(conn=None) -> dict:
    """Return a dict of market-specific values for prompt interpolation.

    When *conn* is None the function falls back to hard-coded Canadian defaults
    so every existing call-site keeps working without a database handle.
    """
    if conn is None:
        return {
            "name": "Canada",
            "code": "CA",
            "spelling": "Use Commonwealth English spelling (e.g. 'flavours', 'vapour', 'favourite', 'colour', 'centre').",
            "adjective": "Canadian",
            "ship": "shipped across Canada",
            "avail": "available in Canada",
            "prompt_fragment": "",
            "store_description": "",
        }
    from shopifyseo.market_context import (
        build_market_prompt_fragment,
        country_display_name,
        get_primary_country_code,
        shipping_cue,
        spelling_variant,
    )

    code = get_primary_country_code(conn)
    name = country_display_name(code)
    ship_phrase, avail_phrase = shipping_cue(code)
    adjective = name
    if code == "US":
        adjective = "American"
    elif code == "GB":
        adjective = "British"
    elif code == "AU":
        adjective = "Australian"
    return {
        "name": name,
        "code": code,
        "spelling": spelling_variant(code),
        "adjective": adjective,
        "ship": ship_phrase,
        "avail": avail_phrase,
        "prompt_fragment": build_market_prompt_fragment(conn),
        "store_description": _load_store_description(conn),
    }


def gsc_segment_evidence_sentence(context: dict, *, compact: bool = False) -> str | None:
    """One evidence-bound sentence from Tier B aggregates (object_context / DB)."""
    summary = context.get("gsc_segment_summary") or {}
    dev = summary.get("device_mix") or []
    countries = summary.get("top_countries") or []
    apps = summary.get("search_appearances") or []
    if not dev and not countries and not apps:
        return None
    parts: list[str] = []
    if dev:
        slice_ = dev[:1] if compact else dev[:2]
        parts.append(
            "device "
            + ", ".join(
                f"{d.get('segment', '')} ~{float(d.get('share') or 0) * 100:.0f}% imp share" for d in slice_
            )
        )
    if countries and not compact:
        parts.append(
            "countries "
            + ", ".join(
                f"{c.get('segment', '')} ({int(c.get('impressions') or 0)} imp)" for c in countries[:3]
            )
        )
    elif countries and compact:
        c = countries[0]
        parts.append(f"top country {c.get('segment', '')} ({int(c.get('impressions') or 0)} imp)")
    if apps and not compact:
        parts.append(
            "appearances "
            + ", ".join(f"{a.get('segment', '')} ({int(a.get('impressions') or 0)} imp)" for a in apps[:2])
        )
    elif apps and compact and not parts:
        a = apps[0]
        parts.append(f"appearance {a.get('segment', '')} ({int(a.get('impressions') or 0)} imp)")
    if not parts:
        return None
    return (
        "Google Search Console segment split (cached, Overview-aligned window): "
        + "; ".join(parts)
        + ". Cite only these buckets; do not invent segments."
    )


def xml_block(tag: str, content: str) -> str:
    return f"<{tag}>\n{content.strip()}\n</{tag}>"


def response_schema(object_type: str, conn=None) -> dict:
    """Build a JSON Schema for OpenAI structured output with length constraints on SEO fields."""
    title_min = TITLE_TARGET_MIN.get(object_type, 45)
    title_max = TITLE_LIMIT
    desc_min = DESCRIPTION_TARGET_MIN.get(object_type, 145)
    desc_max = DESCRIPTION_LIMIT
    body_min = BODY_MIN_LENGTH.get(object_type, 300)

    properties = {
        "seo_title": {
            "type": "string",
            "description": f"SEO title. Target {title_min}-{title_max} characters, maximizing toward {title_max} when possible. Must not exceed {title_max}.",
            "minLength": title_min,
            "maxLength": title_max,
        },
        "seo_description": {
            "type": "string",
            "description": f"SEO meta description. Target {desc_min}-{desc_max} characters, maximizing toward {desc_max}. Fill the space with relevant detail — short descriptions hurt CTR. Count every character including spaces.",
            "minLength": desc_min,
            "maxLength": desc_max,
        },
        "body": {
            "type": "string",
            "description": f"Body HTML content. Minimum {body_min} characters.",
            "minLength": body_min,
        },
    }
    required = ["seo_title", "seo_description", "body"]

    if object_type == "product":
        properties["tags"] = {
            "type": "string",
            "description": "Comma-separated taxonomy tags.",
        }
        required.append("tags")

    if object_type == "blog_article":
        m = _market_ctx(conn)
        properties["title"] = {
            "type": "string",
            "description": (
                "Article title shown to readers as the H1 headline. "
                "20–70 characters. Keyword-led and specific. "
                f"{m['spelling']} "
                "No ALL CAPS words. No weakening parentheticals. "
                "Distinct from seo_title — this is the visible headline, not the meta tag."
            ),
            "minLength": 20,
            "maxLength": 70,
        }
        required.insert(0, "title")

    return {
        "name": f"{object_type}_seo_recommendation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def single_field_response_schema(object_type: str, field: str) -> dict:
    """Build a JSON Schema for a single-field regeneration response."""
    title_min = TITLE_TARGET_MIN.get(object_type, 45)
    title_max = TITLE_LIMIT
    desc_min = DESCRIPTION_TARGET_MIN.get(object_type, 145)
    desc_max = DESCRIPTION_LIMIT
    body_min = BODY_MIN_LENGTH.get(object_type, 300)

    if field == "seo_title":
        field_schema = {
            "type": "string",
            "description": f"SEO title. Target {title_min}-{title_max} characters, maximizing toward {title_max} when possible. Must not exceed {title_max}.",
            "minLength": title_min,
            "maxLength": title_max,
        }
    elif field == "seo_description":
        field_schema = {
            "type": "string",
            "description": f"SEO meta description. Target {desc_min}-{desc_max} characters, maximizing toward {desc_max}. Fill the space with relevant detail — short descriptions hurt CTR. Must not exceed {desc_max}.",
            "minLength": desc_min,
            "maxLength": desc_max,
        }
    elif field == "body":
        field_schema = {
            "type": "string",
            "description": f"Body HTML content. Minimum {body_min} characters.",
            "minLength": body_min,
        }
    else:
        field_schema = {"type": "string"}

    return {
        "name": f"{object_type}_{field}_regeneration",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {field: field_schema},
            "required": [field],
            "additionalProperties": False,
        },
    }


def _review_fields_schema(field_list: list[str]) -> dict:
    """Build the _review object schema with explicit per-field enum properties."""
    action_enum = {"type": "string", "enum": ["approved", "improved", "rewritten"]}
    return {
        "type": "object",
        "properties": {f: action_enum for f in field_list},
        "required": field_list,
        "additionalProperties": False,
    }


def review_response_schema(object_type: str) -> dict:
    """Build a JSON Schema for the review pass response."""
    base = response_schema(object_type)
    field_list = list(base["schema"]["required"])
    base["schema"]["properties"]["_review"] = _review_fields_schema(field_list)
    base["schema"]["required"].append("_review")
    base["name"] = f"{object_type}_seo_review"
    return base


def field_review_response_schema(object_type: str, field: str) -> dict:
    """Build a JSON Schema for the single-field review response."""
    base = single_field_response_schema(object_type, field)
    base["schema"]["properties"]["_review"] = _review_fields_schema([field])
    base["schema"]["required"].append("_review")
    base["name"] = f"{object_type}_{field}_review"
    return base


def object_field_instructions(object_type: str, conn=None) -> str:
    from .config import get_store_identity
    _store_name, _store_domain = get_store_identity()
    _brand = _store_name or "the store"
    _brand_suffix = f" | {_brand}"
    _brand_suffix_len = len(_brand_suffix)
    m = _market_ctx(conn)

    if object_type == "product":
        return (
            f"seo_title: Fill the 50–65 character range, maximizing toward 65 characters when possible. Lead with Brand + Model + Flavour. "
            "After the key descriptor, add a differentiating spec term or product attribute so the brand+model+descriptor block reaches at least 34 characters. "
            f"When space permits, append '{m['name']}' after the spec terms to reinforce geographic targeting. "
            f"Then append '{_brand_suffix}' ({_brand_suffix_len} characters including the space and pipe). "
            f"If the full string with '{_brand_suffix}' exceeds 65 characters, drop '{_brand_suffix}' first; if still over 65, drop '{m['name']}'. "
            f"When '{_brand_suffix}' is dropped, also drop the ' | ' pipe separator — never leave a trailing ' | ' at the end of the title. "
            f"Always maximize the title length toward 65 characters by including additional relevant keywords when space allows, rather than stopping at the minimum. {m['spelling']}\n"
            "seo_description: Target 150–160 characters (hard ceiling is 160). "
            "Maximise toward 160 — short descriptions hurt CTR. "
            "Count every character — spaces included — before finalising. "
            "Lead with the strongest transactional hook — brand name plus device type or flavour — to capture intent immediately. "
            "Add the most compelling differentiator: key product attribute, spec options, or variety. "
            f"End with a natural {m['adjective']} buying signal. {m['spelling']}\n"
            "Do not echo the seo_title verbatim — complement it with secondary intent such as use case, spec detail, or buying trigger.\n"
            "seo_title and seo_description must target complementary keyword angles. Do not repeat the same exact phrase in both. "
            "The title should lead with the primary commercial keyword (brand + model + flavour), while the description should expand on secondary intent "
            "(use case, differentiator, buying trigger) without echoing the title verbatim.\n"
            "body: Minimum 1,500 characters of HTML (schema-enforced). Aim for at least 300 words of visible text. "
            "Must include a dedicated flavour profile section. Use five sections in this order: "
            "(1) answer-first opening of 40–60 words in store voice, "
            "(2) flavour profile with a question-based H2 or H3 heading, "
            "(3) supporting device or spec section, "
            "(4) who-it's-for or related-flavour guidance, "
            "(5) natural internal-link mentions — use only the exact `url` values from approved_internal_link_targets (full store URL strings), "
            "and always include a descriptive title attribute for SEO and accessibility. "
            "For collection links use 'Shop [Title] — Store Name', for product links use '[Title] — Store Name', "
            "for articles use 'Read [Title] — Store Name' "
            "(e.g. `<a href=\"URL\" title=\"Shop Products — {store_name}\">anchor text</a>`). "
            "Make flavour the primary merchandising story and treat specs as supporting context. "
            f"Include a natural {m['adjective']} market reference in the body (e.g. {m['avail']}, {m['adjective']} shoppers, {m['ship']}) — one or two mentions, not forced. {m['spelling']}\n"
            f"tags: Comma-separated taxonomy tags. Include: brand, model family, product type, any relevant product attributes, and '{m['name'].lower()}' as a geographic tag. "
            "Keep them clean, lowercase, and normalized for store filtering and collection logic.\n"
            "Do not rewrite the product title unless explicitly asked. When specs are already repetitive, expand product details, use-case, and shopper-fit language instead of repeating specs.\n"
            "If `cluster_seo_context` is present in <context>, it contains SEO target keywords from a keyword cluster analysis relevant to this product's brand or category. "
            "Higher volume means more search demand; lower difficulty means easier to rank for. "
            "Work in the **primary keyword** plus **as many supporting cluster keywords as read naturally** across headings, body, and lists — "
            "aim for broad topical coverage of the cluster without awkward repetition or stuffing. "
            "If you cannot use an exact phrase, use close natural wording that searchers would still recognize. "
            "Favour higher-volume, lower-difficulty phrases when choosing emphasis.\n"
            "If `keyword_context` is present in <context>, it contains keyword metrics from research mapped to this page. "
            "Use these keywords to inform your word choice for titles, descriptions, headings, and body text. "
            "If `content_format_hint` is provided (e.g. 'faq', 'video_embed', 'direct_answer'), consider adapting the content structure accordingly. "
            "If `competitor_gaps` is present, these are keywords where competitors rank but we don't — prioritise naturally incorporating these where relevant.\n"
            "If `seo_keyword_gaps` is present in <context>, it lists high-priority cluster keywords NOT YET covered on this page, "
            "sorted by opportunity (highest first). Weave `must_consider` keywords into the content as follows:\n"
            "- seo_title: incorporate the 1–2 highest-opportunity keywords naturally within the character limit\n"
            "- seo_description: work in 2–3 must_consider phrases that complement the title\n"
            "- body: weave in as many must_consider phrases as fit naturally; prioritise those listed first\n"
            "- tags: include must_consider keywords as tags where they fit the taxonomy\n"
            "The `already_present` list shows keywords already covered — do NOT repeat these unnecessarily. "
            "Readability and conversion always take priority over keyword density. "
            "Do not repeat keywords unnaturally or force phrases where they break the reading flow."
        )
    if object_type == "collection":
        bmin = BODY_MIN_LENGTH.get("collection", 220)
        return (
            "seo_title: Max 65 characters and focused on category intent. Maximize toward 65 characters when possible. "
            f"When space permits within the 65-character limit, include '{m['name']}' naturally (e.g. 'Best Disposable Vapes {m['name']}'); drop it if the title would exceed the limit.\n"
            f"seo_description: Target 150-160 characters, maximising toward 160. Short descriptions hurt CTR. End with a natural {m['adjective']} buying signal when space allows.\n"
            f"body: Minimum {bmin} characters of HTML (schema-enforced). Build category depth, hub value, and clear collection intent. "
            f"Include a natural {m['adjective']} market reference (e.g. 'Shop [category] online in {m['name']}' or '{m['adjective']} shoppers') — one or two mentions, not forced. "
            "Use H2 or H3 headings for scannability. "
            "MUST include 2–4 contextual internal links as `<a href=\"FULL_URL\" title=\"DESCRIPTIVE_TITLE\">anchor text</a>` where FULL_URL is exactly the `url` field "
            "from the matching entry in `approved_internal_link_targets` in <context> (character-for-character — use the full store URL). "
            "DESCRIPTIVE_TITLE must follow this pattern: 'Shop [Page Title] — Store Name' for collections, '[Page Title] — Store Name' for products, "
            "'Read [Page Title] — Store Name' for articles. "
            "Every <a> tag MUST include a descriptive title attribute for accessibility and SEO. "
            "If that list has fewer than 2 entries, link to every relevant target that appears there; if it is empty, include no in-body links. "
            "Never invent `/collections/`, `/products/`, or `/pages/` paths. "
            "Populate the `internal_links` array with the same exact URLs you used in the body.\n"
            "If `cluster_seo_context` is present in <context>, it contains SEO target keywords from a keyword cluster analysis relevant to this collection. "
            "Higher volume means more search demand; lower difficulty means easier to rank for. "
            "Work in the **primary keyword** plus **as many supporting cluster keywords as read naturally** across headings and body — "
            "aim for broad topical coverage without stuffing. Use close natural wording when an exact phrase would sound forced. "
            "Favour higher-volume, lower-difficulty phrases when choosing emphasis.\n"
            "If `keyword_context` is present, use these research keywords to strengthen targeting. "
            "If `competitor_gaps` is present, prioritise naturally incorporating gap keywords where relevant.\n"
            "If `seo_keyword_gaps` is present in <context>, it lists high-priority cluster keywords NOT YET covered on this collection page, "
            "sorted by opportunity (highest first). Weave `must_consider` keywords into the content as follows:\n"
            "- seo_title: incorporate the 1–2 highest-opportunity keywords naturally within the character limit\n"
            "- seo_description: work in 2–3 must_consider phrases that complement the title\n"
            "- body: weave in as many must_consider phrases as fit naturally; prioritise those listed first\n"
            "The `already_present` list shows keywords already covered — do NOT repeat these unnecessarily. "
            "Readability and conversion always take priority over keyword density. "
            "Do not repeat keywords unnaturally or force phrases where they break the reading flow."
        )
    bmin_page = BODY_MIN_LENGTH.get("page", 300)
    page_like_body = (
        f"body: Minimum {bmin_page} characters of HTML (schema-enforced). Build trust, clarity, and useful detail. "
        f"Where topically relevant, include a natural {m['adjective']} market reference (e.g. 'in {m['name']}', '{m['adjective']} shoppers') — one or two mentions, not forced. "
        "Use H2 or H3 headings where they help readers scan. "
        "MUST include 2–4 contextual internal links as `<a href=\"FULL_URL\" title=\"DESCRIPTIVE_TITLE\">anchor text</a>` where FULL_URL is exactly the `url` field "
        "from the matching entry in `approved_internal_link_targets` in <context> (character-for-character — use the full store URL). "
        "DESCRIPTIVE_TITLE must follow this pattern: 'Shop [Page Title] — Store Name' for collections, '[Page Title] — Store Name' for products, "
        "'Read [Page Title] — Store Name' for articles. "
        "Every <a> tag MUST include a descriptive title attribute for accessibility and SEO. "
        "If that list has fewer than 2 entries, link to every relevant target that appears there; if it is empty, include no in-body links. "
        "Never invent `/collections/`, `/products/`, or `/pages/` paths. "
        "Populate the `internal_links` array with the same exact URLs you used in the body.\n"
        "If `cluster_seo_context` is present in <context>, it contains SEO target keywords from a keyword cluster analysis relevant to this page. "
        "Higher volume means more search demand; lower difficulty means easier to rank for. "
        "Work in the **primary keyword** plus **as many supporting cluster keywords as read naturally** across headings and body — "
        "aim for broad topical coverage without stuffing. Use close natural wording when an exact phrase would sound forced. "
        "Favour higher-volume, lower-difficulty phrases when choosing emphasis.\n"
        "If `keyword_context` is present, use these research keywords to strengthen targeting. "
        "If `competitor_gaps` is present, prioritise naturally incorporating gap keywords where relevant.\n"
        "If `seo_keyword_gaps` is present in <context>, it lists high-priority cluster keywords NOT YET covered on this page, "
        "sorted by opportunity (highest first). Weave `must_consider` keywords into the content as follows:\n"
        "- seo_title: incorporate the 1–2 highest-opportunity keywords naturally within the character limit\n"
        "- seo_description: work in 2–3 must_consider phrases that complement the title\n"
        "- body: weave in as many must_consider phrases as fit naturally; prioritise those listed first\n"
        "The `already_present` list shows keywords already covered — do NOT repeat these unnecessarily. "
        "Readability and conversion always take priority over keyword density. "
        "Do not repeat keywords unnaturally or force phrases where they break the reading flow.\n"
    )
    if object_type == "blog_article":
        return (
            "title: The article headline shown to readers as the H1. "
            "20–70 characters. Keyword-led and specific — lead with the primary topic (e.g. a flavor name, product category, or shopping question). "
            f"{m['spelling']} "
            "No ALL CAPS words. No weakening parentheticals like '(Full Guide)' or '(Updated)'. "
            "This is distinct from seo_title — it is the visible H1, not the meta tag.\n"
            "seo_title: Max 65 characters, aligned to the article topic and search intent. Maximize toward 65 characters when possible. "
            f"When space permits, include '{m['name']}' naturally to reinforce geographic targeting; drop it if the title would exceed the limit.\n"
            f"seo_description: Target 150-160 characters, maximising toward 160. Short descriptions hurt CTR. End with a natural {m['adjective']} buying or reading signal when space allows.\n"
            + page_like_body.replace(
                "Build trust, clarity, and useful detail.",
                "Build editorial depth for this blog post (news, guide, or story — not generic product-SKU copy).",
            )
        )
    return (
        "seo_title: Max 65 characters and aligned to the page's real intent. Maximize toward 65 characters when possible. "
        f"When space permits, include '{m['name']}' naturally to reinforce geographic targeting; drop it if the title would exceed the limit.\n"
        f"seo_description: Target 150-160 characters, maximising toward 160. Short descriptions hurt CTR. End with a natural {m['adjective']} signal when space allows.\n"
        + page_like_body.replace(
            "Build trust, clarity, and useful detail.",
            "Build trust, clarity, and useful detail for this page's real intent (brand, guide, policy, support — not generic product copy). ",
        )
    )


def formatting_instructions(object_type: str, field_list: list[str]) -> str:
    link_rule = (
        "Every `<a href=\"...\">` in `body` must use the exact `url` string from an entry in `approved_internal_link_targets` "
        "(same string as in context JSON — typically /collections/…, /products/…, or /pages/… URLs). "
        "Every `<a>` tag MUST also include a descriptive `title` attribute for accessibility and SEO. "
        "Use 'Shop [Title] — Store Name' for collections, '[Title] — Store Name' for products, 'Read [Title] — Store Name' for articles "
        "(e.g. `<a href=\"URL\" title=\"Shop Products — {store_name}\">anchor text</a>`). "
        "If `approved_internal_link_targets` is empty, omit in-body internal links. "
        "The `internal_links` array must list only those same URLs (strings) — never invent handles or paths.\n"
    )
    return (
        f"Return one JSON object with exactly these top-level keys: {', '.join(field_list)}.\n"
        "`seo_title`, `seo_description`, `body`, and (for products) `tags` must be strings.\n"
        "Body must be valid HTML suitable for Shopify.\n"
        + link_rule
        + "The response schema enforces character limits on seo_title and seo_description — maximize seo_title toward the upper limit (65 characters) when content allows, and fill the allowed range fully.\n"
        "seo_title and seo_description must target complementary keyword angles. Do not repeat the same exact phrase in both. "
        "The title should lead with the primary commercial keyword (brand + model + flavor), while the description should expand on secondary intent "
        "(use case, differentiator, buying trigger) without echoing the title verbatim.\n"
        f"For {object_type}s, body HTML should be commercially specific to the exact intent.\n"
        "Do not include markdown fences. Do not echo the prompt. Do not explain outside the JSON."
    )


def single_field_formatting_instructions(field: str) -> str:
    return f"Return one JSON object with exactly one key: '{field}'. The value must be a string."


def single_field_task_instruction(object_type: str, field: str, conn=None) -> str:
    # For all core fields the instruction is present in the <field_instructions> block;
    # repeating it inline in the task string doubles the prompt without adding signal.
    if field in ("seo_title", "seo_description", "body", "tags"):
        return f"Generate a new {field} for this {object_type}. Follow the <field_instructions> block exactly."
    instructions = object_field_instructions(object_type, conn=conn)
    for line in instructions.splitlines():
        if line.startswith(f"{field}:"):
            return f"Generate a new {field} for this {object_type}. Follow this field instruction exactly: {line}"
    return f"Generate a new value for the '{field}' field."


def single_field_specific_instructions(object_type: str, field: str, conn=None) -> str:
    instructions = object_field_instructions(object_type, conn=conn)
    matched_lines = [line for line in instructions.splitlines() if line.startswith(f"{field}:")]
    if field == "seo_title":
        matched_lines.append(
            "Do not include internal links, workflow notes, or body-writing strategy in the output. "
            "Focus only on the strongest title based on the core product facts and accepted sibling fields."
        )
    return "\n".join(matched_lines) if matched_lines else instructions


_RAG_SLIM_CHAR_CAP = 1_600   # ~400 tokens
_RAG_FULL_CHAR_CAP = 3_200   # ~800 tokens


def _inject_slim_rag_context(result: dict, full_context: dict, max_similar: int = 3, max_kw: int = 5) -> dict:
    """Inject RAG context keys into a slim context dict, respecting token caps."""
    import json as _json
    rce = full_context.get("related_content_examples") or []
    if rce:
        slimmed = rce[:max_similar]
        serialized = _json.dumps(slimmed, ensure_ascii=True)
        if len(serialized) <= _RAG_SLIM_CHAR_CAP:
            result["related_content_examples"] = slimmed
    ako = full_context.get("additional_keyword_opportunities") or []
    if ako:
        slimmed = ako[:max_kw]
        serialized = _json.dumps(slimmed, ensure_ascii=True)
        if len(serialized) <= _RAG_SLIM_CHAR_CAP:
            result["additional_keyword_opportunities"] = slimmed
    return result


def _slim_seo_description_context(object_type: str, full_context: dict) -> dict:
    """Return a slimmed prompt context for seo_description single-field regeneration.

    Keeps: product identity, key specs, GSC CTR/position signals, and accepted
    seo_title for complementarity checking.
    Drops: internal-link targets, related collections/products, recommendation
    history, query clusters, raw evidence, and body content — none are needed
    to craft a 155-character description string.
    """
    primary = dict(full_context.get("primary_object") or {})
    specs = dict(primary.get("specs") or {})
    intent = dict(primary.get("intent") or {})

    slim_specs = {
        "brand": specs.get("brand") or "",
        "model": specs.get("model") or "",
        "flavor": specs.get("flavor") or "",
        "nicotine_strength": specs.get("nicotine_strength") or "",
        "puff_count": specs.get("puff_count") or "",
        "device_type": specs.get("device_type") or "",
        "e_liquid_flavor_labels": specs.get("e_liquid_flavor_labels") or [],
    }
    slim_primary = {
        "title": primary.get("title") or "",
        "current_seo_title": primary.get("current_seo_title") or "",
        "current_seo_description": primary.get("current_seo_description") or "",
        "specs": slim_specs,
        "intent": {
            # primary_terms: search-ready compound terms for targeting.
            "primary_terms": intent.get("primary_terms") or [],
            # canada_keywords: Canadian market signals for the buying hook.
            "canada_keywords": (intent.get("canada_keywords") or [])[:3],
            "flavor_family": intent.get("flavor_family") or "",
        },
    }

    seo_context = dict(full_context.get("seo_context") or {})
    current_fields = dict(seo_context.get("current_fields") or {})
    slim_seo_context = {
        "current_fields": {
            # Include both so the generator can check complementarity with the live title.
            "seo_title": current_fields.get("seo_title") or "",
            "seo_description": current_fields.get("seo_description") or "",
        },
        "seo_fact_summary": {
            # CTR and position are the two primary levers for meta description optimisation.
            "gsc_impressions": (seo_context.get("seo_fact_summary") or {}).get("gsc_impressions"),
            "gsc_ctr": (seo_context.get("seo_fact_summary") or {}).get("gsc_ctr"),
            "gsc_position": (seo_context.get("seo_fact_summary") or {}).get("gsc_position"),
            "index_status": (seo_context.get("seo_fact_summary") or {}).get("index_status"),
        },
    }

    result = {
        "object_type": object_type,
        "primary_object": slim_primary,
        "seo_context": slim_seo_context,
    }
    cluster_ctx = full_context.get("cluster_seo_context")
    if cluster_ctx:
        result["cluster_seo_context"] = cluster_ctx
    gaps = full_context.get("seo_keyword_gaps")
    if gaps:
        result["seo_keyword_gaps"] = gaps
    ss = full_context.get("gsc_segment_summary")
    if ss:
        result["gsc_segment_summary"] = ss
    sqk = full_context.get("segment_query_keywords") or []
    if sqk:
        result["segment_query_keywords"] = sqk[:5]
    kw_ctx = full_context.get("keyword_context") or []
    if kw_ctx:
        result["keyword_context"] = _slim_keyword_context(kw_ctx, max_rows=5)
    result = _inject_slim_rag_context(result, full_context)
    hq = _gsc_query_highlights_for_slim(full_context)
    if hq:
        result["gsc_query_highlights"] = hq
    return result


def _slim_tags_context(object_type: str, full_context: dict) -> dict:
    """Slim context for tags generation — only product identity and taxonomy signals needed."""
    primary = dict(full_context.get("primary_object") or {})
    specs = dict(primary.get("specs") or {})
    intent = dict(primary.get("intent") or {})
    result = {
        "primary_object": {
            "title": primary.get("title") or "",
            "current_tags": specs.get("tags") or primary.get("tags") or "",
            "specs": {
                "brand": specs.get("brand") or "",
                "model": specs.get("model") or "",
                "flavor": specs.get("flavor") or "",
                "nicotine_strength": specs.get("nicotine_strength") or "",
                "puff_count": specs.get("puff_count") or "",
                "device_type": specs.get("device_type") or "",
                "e_liquid_flavor_labels": specs.get("e_liquid_flavor_labels") or [],
                "vaping_style_labels": specs.get("vaping_style_labels") or [],
            },
            "intent": {
                "flavor_family": intent.get("flavor_family") or "",
                "primary_terms": (intent.get("primary_terms") or [])[:5],
            },
        }
    }
    kw_ctx = full_context.get("keyword_context") or []
    if kw_ctx:
        result["keyword_context"] = _slim_keyword_context(kw_ctx, max_rows=5)
    result = _inject_slim_rag_context(result, full_context)
    return result


def slim_single_field_prompt_context(object_type: str, field: str, full_context: dict) -> dict:
    if field == "seo_description":
        return _slim_seo_description_context(object_type, full_context)
    if field == "tags":
        return _slim_tags_context(object_type, full_context)
    if field != "seo_title":
        return full_context

    primary = dict(full_context.get("primary_object") or {})
    specs = dict(primary.get("specs") or {})
    intent = dict(primary.get("intent") or {})

    for key in (
        "battery_type_labels",
        "coil_connection_labels",
        "color_pattern_labels",
        "vaporizer_style_labels",
        "vaping_style_labels",
        "resolved_attributes",
        "tags",
        "variant_titles",
        "inventory",
        "online_store_url",
    ):
        specs.pop(key, None)

    for key in (
        "query_cluster_summary",
        "canada_keywords",
    ):
        intent.pop(key, None)

    slim_primary = {
        # title is the canonical product name — kept as the primary identity signal.
        "title": primary.get("title") or "",
        # handle and vendor omitted: handle is a URL slug (irrelevant to title text),
        # vendor duplicates specs.brand exactly.
        "current_seo_title": primary.get("current_seo_title") or "",
        # current_seo_description omitted: description content has no bearing on title construction.
        "specs": {
            "brand": specs.get("brand") or "",
            "model": specs.get("model") or "",
            "flavor": specs.get("flavor") or "",
            "nicotine_strength": specs.get("nicotine_strength") or "",
            "puff_count": specs.get("puff_count") or "",
            "device_type": specs.get("device_type") or "",
            # battery_size, charging_port, coil, size omitted: none of these appear in SEO titles.
            "e_liquid_flavor_labels": specs.get("e_liquid_flavor_labels") or [],
        },
        "intent": {
            # intent_labels omitted: they are meta-labels describing the other fields,
            # not actual query terms — the model doesn't need them when primary_terms is present.
            "primary_terms": intent.get("primary_terms") or [],
            "flavor_family": intent.get("flavor_family") or "",
        },
    }

    seo_context = dict(full_context.get("seo_context") or {})
    current_fields = dict(seo_context.get("current_fields") or {})
    slim_seo_context = {
        "current_fields": {
            # product_title omitted: exact duplicate of primary_object.title above.
            "seo_title": current_fields.get("seo_title") or "",
        },
        "seo_fact_summary": {
            # score and priority omitted: internal dashboard metrics, not useful to the model.
            "gsc_impressions": (seo_context.get("seo_fact_summary") or {}).get("gsc_impressions"),
            "gsc_ctr": (seo_context.get("seo_fact_summary") or {}).get("gsc_ctr"),
            "gsc_position": (seo_context.get("seo_fact_summary") or {}).get("gsc_position"),
            "index_status": (seo_context.get("seo_fact_summary") or {}).get("index_status"),
        },
    }

    result = {
        "object_type": object_type,
        "primary_object": slim_primary,
        "seo_context": slim_seo_context,
        "catalog_title_examples": full_context.get("catalog_title_examples") or [],
    }
    cluster_ctx = full_context.get("cluster_seo_context")
    if cluster_ctx:
        result["cluster_seo_context"] = cluster_ctx
    gaps = full_context.get("seo_keyword_gaps")
    if gaps:
        result["seo_keyword_gaps"] = gaps
    ss = full_context.get("gsc_segment_summary")
    if ss:
        result["gsc_segment_summary"] = ss
    sqk = full_context.get("segment_query_keywords") or []
    if sqk:
        result["segment_query_keywords"] = sqk[:4]
    kw_ctx = full_context.get("keyword_context") or []
    if kw_ctx:
        result["keyword_context"] = _slim_keyword_context(kw_ctx, max_rows=3)
    result = _inject_slim_rag_context(result, full_context)
    return result


def build_signal_narrative(context: dict, *, primary_object: dict | None = None, conn=None) -> str:
    m = _market_ctx(conn)
    fact = context.get("fact") or {}
    detail_payload = context.get("detail") or {}
    primary = detail_payload.get("product") or detail_payload.get("collection") or detail_payload.get("page") or {}
    object_type = context.get("object_type") or "page"
    current_body = str(primary.get("description_html") or primary.get("body") or "")
    body_words = word_count(current_body)
    lines: list[str] = []
    if object_type == "product":
        # primary_object should always be provided from prompt_context_dict to avoid redundant curated_primary_object call
        # If None, this indicates a code path that should be updated to pass prompt_context_precomputed
        if primary_object is None:
            raise ValueError("primary_object must be provided. Use prompt_context_precomputed from prompt_context() and pass primary_object from prompt_context_dict.")
        primary_obj = primary_object
        specs = primary_obj.get("specs") or {}
        intent = primary_obj.get("intent") or {}
        brand = str(specs.get("brand") or primary.get("vendor") or "This").strip()
        flavor = str(specs.get("flavor") or primary.get("title") or "").strip()
        device_type = str(specs.get("device_type") or primary.get("product_type") or "").strip()
        flavor_family = str(intent.get("flavor_family") or "other").strip()
        lines.append(f"This SKU should rank as a {m['adjective']} commercial product page for {brand} {device_type} intent, with flavor-led differentiation centered on {flavor or primary.get('title')}. The dominant flavor family is {flavor_family}.")
    else:
        lines.append(f"This {object_type} page needs stronger {m['adjective']} commercial clarity and better support for discovery across the store catalog.")
    impressions = int(fact.get("gsc_impressions") or 0)
    clicks = int(fact.get("gsc_clicks") or 0)
    ctr = float(fact.get("gsc_ctr") or 0)
    position = float(fact.get("gsc_position") or 0)
    if impressions > 0:
        if ctr and ctr < 1.5:
            lines.append(f"The page already earns {impressions} impressions but CTR is weak, so the title and meta should sharpen commercial relevance and click appeal.")
        elif position and position >= 8:
            lines.append(f"The page shows some search visibility with an average position near {position:.1f}, so stronger on-page alignment and internal links can help it move into a better click range.")
        else:
            lines.append(f"Search visibility exists with {impressions} impressions and {clicks} clicks, so the rewrite should improve depth and relevance without drifting away from the confirmed intent.")
    else:
        lines.append("Query-level search data is limited, so the recommendation should lean on confirmed product facts, flavor intent, and catalog relationships instead of generic SEO filler.")
    seg_sentence = gsc_segment_evidence_sentence(context, compact=False)
    if seg_sentence:
        lines.append(seg_sentence)
    index_status = str(fact.get("index_status") or "").strip()
    if index_status and "indexed" not in index_status.lower():
        lines.append(f"Index coverage is not fully healthy ({index_status}), so the page needs clearer answer-first copy and stronger trust signals rather than thin or vague text.")
    pagespeed = fact.get("pagespeed_performance")
    if pagespeed not in (None, ""):
        try:
            pagespeed_value = int(float(pagespeed))
        except (TypeError, ValueError):
            pagespeed_value = 0
        if pagespeed_value and pagespeed_value < 70:
            lines.append(f"PageSpeed performance is only {pagespeed_value}, so the copy should stay structured, scan-friendly, and immediately useful in the opening section.")
    if body_words < 300:
        lines.append(f"The current body is only about {body_words} words, which is too thin for a competitive product page; it needs a fuller answer-first opening, richer flavor detail, and a clearer suitability section.")
    elif body_words < 450:
        lines.append(f"The current body has some substance at roughly {body_words} words, but it still needs more differentiated flavor and buying-context depth to outperform similar SKU pages.")
    internal_links = int(fact.get("internal_link_count") or 0)
    if internal_links < 2:
        lines.append("Internal-link support is light, so the recommendation should naturally point shoppers toward closely related brand, flavor-family, or device-family pages.")
    return " ".join(lines[:8])


def build_title_signal_narrative(context: dict, *, primary_object: dict | None = None, conn=None) -> str:
    """Trimmed signal narrative used only for seo_title regeneration.

    Keeps the two sentences that are directly load-bearing for title construction:
    (1) product / market positioning, (2) GSC data availability.
    All body-copy, internal-link, and word-count sentences are intentionally
    excluded — they are irrelevant to a 65-character title string.
    """
    m = _market_ctx(conn)
    fact = context.get("fact") or {}
    detail_payload = context.get("detail") or {}
    primary = detail_payload.get("product") or detail_payload.get("collection") or detail_payload.get("page") or {}
    object_type = context.get("object_type") or "page"
    lines: list[str] = []
    if object_type == "product":
        # primary_object should always be provided from prompt_context_dict to avoid redundant curated_primary_object call
        # If None, this indicates a code path that should be updated to pass prompt_context_precomputed
        if primary_object is None:
            raise ValueError("primary_object must be provided. Use prompt_context_precomputed from prompt_context() and pass primary_object from prompt_context_dict.")
        primary_obj = primary_object
        specs = primary_obj.get("specs") or {}
        intent = primary_obj.get("intent") or {}
        brand = str(specs.get("brand") or primary.get("vendor") or "This").strip()
        flavor = str(specs.get("flavor") or primary.get("title") or "").strip()
        device_type = str(specs.get("device_type") or primary.get("product_type") or "").strip()
        flavor_family = str(intent.get("flavor_family") or "other").strip()
        lines.append(
            f"This SKU should rank as a {m['adjective']} commercial product page for {brand} {device_type} intent, "
            f"with flavor-led differentiation centered on {flavor or primary.get('title')}. "
            f"The dominant flavor family is {flavor_family}."
        )
    else:
        lines.append(f"This {object_type} page needs stronger {m['adjective']} commercial clarity.")
    # Only the GSC availability signal matters for title targeting; omit body/link advice.
    impressions = int(fact.get("gsc_impressions") or 0)
    if impressions > 0:
        ctr = float(fact.get("gsc_ctr") or 0)
        lines.append(
            f"The page has {impressions} GSC impressions"
            + (f" but a weak CTR of {ctr:.1%}, so the title should sharpen commercial relevance." if ctr < 0.015 else ".")
        )
    else:
        lines.append("No GSC data exists yet; base the title entirely on confirmed product facts.")
    seg = gsc_segment_evidence_sentence(context, compact=True)
    if seg:
        lines.append(seg)
    return " ".join(lines)


def build_description_signal_narrative(context: dict, *, primary_object: dict | None = None, conn=None) -> str:
    """Focused signal narrative for seo_description regeneration.

    Keeps the product positioning sentence and the GSC CTR/position signal
    (directly relevant to meta description click optimisation). Drops body word
    count, internal links, and pagespeed — none of which affect a 155-character description.
    """
    m = _market_ctx(conn)
    fact = context.get("fact") or {}
    detail_payload = context.get("detail") or {}
    primary = detail_payload.get("product") or detail_payload.get("collection") or detail_payload.get("page") or {}
    object_type = context.get("object_type") or "page"
    lines: list[str] = []
    if object_type == "product":
        # primary_object should always be provided from prompt_context_dict to avoid redundant curated_primary_object call
        # If None, this indicates a code path that should be updated to pass prompt_context_precomputed
        if primary_object is None:
            raise ValueError("primary_object must be provided. Use prompt_context_precomputed from prompt_context() and pass primary_object from prompt_context_dict.")
        primary_obj = primary_object
        specs = primary_obj.get("specs") or {}
        intent = primary_obj.get("intent") or {}
        brand = str(specs.get("brand") or primary.get("vendor") or "This").strip()
        flavor = str(specs.get("flavor") or primary.get("title") or "").strip()
        device_type = str(specs.get("device_type") or primary.get("product_type") or "").strip()
        flavor_family = str(intent.get("flavor_family") or "other").strip()
        lines.append(
            f"This SKU should rank as a {m['adjective']} commercial product page for {brand} {device_type} intent, "
            f"with flavor-led differentiation centered on {flavor or primary.get('title')}. "
            f"The dominant flavor family is {flavor_family}."
        )
    else:
        lines.append(f"This {object_type} page needs a stronger commercial meta description for {m['adjective']} shoppers.")
    # GSC signal: CTR and position are the primary levers for meta description optimisation.
    impressions = int(fact.get("gsc_impressions") or 0)
    clicks = int(fact.get("gsc_clicks") or 0)
    ctr = float(fact.get("gsc_ctr") or 0)
    position = float(fact.get("gsc_position") or 0)
    if impressions > 0:
        if ctr and ctr < 0.04:
            lines.append(
                f"The page has {impressions} impressions but CTR is only {ctr:.1%} — "
                "the meta description must sharpen the commercial hook and click appeal immediately."
            )
        elif position and position >= 8:
            lines.append(
                f"The page sits around position {position:.1f} with {impressions} impressions — "
                "the description should reinforce the strongest transactional signal to improve CTR as ranking improves."
            )
        else:
            lines.append(
                f"Search visibility exists ({impressions} impressions, {clicks} clicks) — "
                "the description rewrite should consolidate that intent without drifting from the confirmed hook."
            )
    else:
        lines.append("No GSC data exists yet; base the description entirely on confirmed product facts and the strongest commercial hook.")
    seg = gsc_segment_evidence_sentence(context, compact=True)
    if seg:
        lines.append(seg)
    return " ".join(lines)


def version_specific_guidance(prompt_version: str, object_type: str) -> str:
    if prompt_version == "v3":
        return (
            "Prompt-generation rules:\n"
            "- Prefer curated structured facts over raw payload interpretation.\n"
            "- If search-demand signals are missing or stale, say so indirectly in `why` and avoid pretending query certainty.\n"
            "- Reuse prior recommendation history only to avoid regressions; do not repeat old wording mechanically.\n"
            "- Body recommendations must add differentiated commercial value, not just restate product specs.\n"
            "- Internal links should reflect real collections/pages already present in the provided relationships.\n\n"
        )
    if prompt_version == "v2":
        return "Use the improved structured-facts prompt profile.\n\n"
    return ""


def profile_instructions(prompt_profile: str, object_type: str) -> str:
    if prompt_profile == "ranking_aggressive":
        return "Use an aggressive ranking-improvement style. Favor the strongest plausible CTR, targeting, content-depth, and internal-link improvements supported by the evidence. Avoid timid generic phrasing."
    if prompt_profile == "balanced":
        return "Use a balanced editorial style that improves rankings while keeping the copy conservative and brand-safe."
    return f"Use the prompt profile '{prompt_profile}' while still prioritizing evidence-driven SEO improvements."


def system_prompt(object_type: str, prompt_profile: str, conn=None) -> str:
    from .config import get_store_identity
    _store_name, _ = get_store_identity()
    _brand = _store_name or "the store"
    m = _market_ctx(conn)

    common = xml_block("role", f"You are the senior SEO strategist for {_brand}. You specialize in ranking product, collection, and brand pages for commercial-intent searches. You are not a generic copywriter. You optimize for the highest-likelihood ranking gains from the evidence provided.")
    constraints = xml_block("constraints", f"Use only the provided facts. Do not invent data, rankings, product specs, shipping promises, or trust claims. Prefer exact commercial phrasing, strong transactional alignment, internal-link clarity, adult-consumer compliance, and valid JSON output only. Do not make health, smoking cessation, or medical claims. Do not recommend awkward repetition, title stuffing, unnatural {m['name']} repetition, redundant tags, or placeholders.")
    eeat = xml_block("eeat", f"Write with store-level experience, expertise, authority, and trust. Use natural store voice such as {_brand}, we, or our when appropriate. Use correct product terminology from the provided specs. Weave in trust and buying signals naturally when supported, such as market relevance, authentic product sourcing, and shipping expectations.")
    geo = xml_block("geo", "Optimize body content for AI-search citability. Use an answer-first opening, question-based H2 or H3 headings where natural, and self-contained passages that can be extracted cleanly by AI Overviews or answer engines.")
    profile = xml_block("profile", profile_instructions(prompt_profile, object_type))
    if object_type == "product":
        object_specific = xml_block("object", f"Produce product-level recommendations with product title, SEO title, meta description, body HTML, tags, rationale, priority actions, and internal-link recommendations. Optimize for model + flavor + {m['name']} transactional queries, SKU-specific relevance, product-title clarity, and product-page CTR.")
    elif object_type == "collection":
        object_specific = xml_block("object", "Produce collection-level recommendations with SEO title, meta description, body HTML, rationale, priority actions, and internal-link recommendations. Optimize for broad commercial category intent, brand/model collection intent, and internal-link hub value.")
    elif object_type == "blog_article":
        object_specific = xml_block("object", "Produce blog-article recommendations with article title (H1 headline), SEO title, meta description, body HTML, rationale, priority actions, and internal-link recommendations. Optimize for editorial search intent, topical depth, and natural links to relevant collections, products, or pages.")
    else:
        object_specific = xml_block("object", "Produce page-level recommendations with SEO title, meta description, body HTML, rationale, priority actions, and internal-link recommendations. Optimize for brand, guide, trust, and supporting informational intent that strengthens transactional pages.")
    return "\n".join([common, constraints, eeat, geo, profile, object_specific])


def review_system_prompt(conn=None) -> str:
    from .config import get_store_identity
    _store_name, _ = get_store_identity()
    _brand = _store_name or "the store"
    m = _market_ctx(conn)

    return "\n".join([
        xml_block("role", f"You are the QA reviewer for {_brand}'s SEO recommendation engine. You receive a draft recommendation generated by a junior model and decide for each field whether to APPROVE it as-is, IMPROVE it with targeted edits, or REWRITE it from scratch. You are the final editorial gate before recommendations are shown to the operator."),
        xml_block("constraints", f"Preserve any field that is already strong. Only touch fields that have clear problems: generic phrasing, missed brand/flavor, length violations, weak {m['adjective']} targeting, spec-heavy body, or poor opening structure. Return valid JSON only. Do not invent data. Do not add health claims. Keep improvements grounded in the original context."),
        xml_block("output_format", "Return one JSON object with the same top-level keys as the input draft. For each field, return the final value (approved original or your improved version). Add a top-level key '_review' that is an object mapping each field name to one of: 'approved', 'improved', or 'rewritten'."),
    ])


def review_user_prompt(
    object_type: str,
    draft_json: str,
    context: dict,
    qa_feedback: str,
    *,
    prompt_context_dict: dict | None = None,
    signal_narrative_str: str | None = None,
    conn=None,
) -> str:
    m = _market_ctx(conn)
    # Extract primary_object from prompt_context_dict if available to avoid redundant computation
    primary_object_from_context = None
    if prompt_context_dict is not None:
        primary_object_from_context = prompt_context_dict.get("primary_object")
    signal_narrative = signal_narrative_str if signal_narrative_str is not None else build_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    pc = prompt_context_dict if prompt_context_dict is not None else prompt_context(context)
    context_json = json.dumps(pc, ensure_ascii=True)
    sections = [
        xml_block("task", f"Review this {object_type} SEO recommendation draft. For each field, decide: approve (strong as-is), improve (targeted fix), or rewrite (too weak). Focus on: title length/brand coverage, meta description commercial hook and length (aim for 150-160 chars), body flavour depth vs spec repetition, {m['adjective']} targeting, and opening originality. If the draft's seo_title exceeds 65 characters, shorten it while preserving the best wording. If seo_description exceeds 160 characters, shorten it; if under 150 characters, expand it with relevant detail — short descriptions hurt CTR. Use Commonwealth English spelling (flavour, vapour, colour). Prefer seo_title lengths closer to 65 characters when additional relevant keywords are available."),
        xml_block("signal_narrative", signal_narrative),
        xml_block("qa_feedback", qa_feedback or "No specific QA issues flagged."),
        xml_block("draft", draft_json),
        xml_block("context", context_json),
    ]
    return "\n\n".join(sections)


def field_system_prompt(object_type: str, field: str, prompt_profile: str, conn=None) -> str:
    from .config import get_store_identity
    _store_name, _ = get_store_identity()
    _brand = _store_name or "the store"
    m = _market_ctx(conn)

    # Build brand voice block for products when store_description is available
    _brand_voice_block = ""
    if object_type == "product" and m.get("store_description"):
        _brand_voice_block = (
            f" Brand positioning for {_brand} (reflect this voice naturally; do not quote verbatim): "
            f"{m['store_description']}"
        )

    # Market context block for products
    _market_block = ""
    if object_type == "product" and m.get("prompt_fragment"):
        _market_block = f" {m['prompt_fragment']}"

    if field == "seo_title":
        return "\n".join([
            xml_block("role", (
                f"You are the senior SEO strategist for {_brand}. "
                "Your sole task is to write one SEO title string for a single product, collection, or page. "
                "Output nothing except that title inside the required JSON object."
                + _brand_voice_block
            )),
            xml_block("constraints", (
                "Use only the confirmed facts provided in <context>. "
                f"Do not invent specs, flavours, puff counts, or claims. "
                "The title must be plain text — no HTML, no markdown, no line breaks. "
                f"The only permitted non-word punctuation is a single ' | ' separator before the {_brand} suffix. "
                "Never end the title with ' | ' or ' |' — if the brand suffix is omitted, omit the pipe separator too. "
                "CRITICAL: If the product name already includes a puff count (e.g. '4K', '10K', '10000'), do NOT repeat it as 'X Puffs' in the title — "
                "this creates redundancy like 'Draggg 4K ... 4000 Puffs' which hurts readability and wastes character space. "
                f"{m['spelling']} "
                "Return valid JSON only."
                + _market_block
            )),
            xml_block("profile", profile_instructions(prompt_profile, object_type)),
        ])
    if field == "seo_description":
        # Build object-type-specific task phrasing
        if object_type == "product":
            task_noun = "product"
            detail_hint = "a relevant confirmed product detail (spec, flavour, or Canada benefit)"
        elif object_type == "collection":
            task_noun = "collection"
            detail_hint = "a relevant collection detail (variety, brand coverage, or Canada relevance)"
        else:
            task_noun = "page"
            detail_hint = "a relevant page detail (purpose, benefit, or Canada relevance)"
        
        return "\n".join([
            xml_block("role", (
                f"You are the senior SEO strategist for {_brand}. "
                f"Your sole task is to write one {task_noun} SEO meta description using only the supplied facts. "
                "Output nothing except that description inside the required JSON object."
                + _brand_voice_block
            )),
            xml_block("length_requirement", (
                "LENGTH IS CRITICAL — read this first:\n"
                "• 150–160 characters inclusive, counting spaces and punctuation.\n"
                "• Aim for 155 characters.\n"
                "• Count only the description text, excluding JSON syntax.\n"
                f"• If too short, add {detail_hint}.\n"
                "• If too long, rewrite more compactly while preserving complete sentences.\n"
                "• Never invent facts or add filler to meet the length."
            )),
            xml_block("examples", (
                f"EXAMPLE 1 ({SEO_DESCRIPTION_EXAMPLE_1_LEN} chars):\n"
                f'"{SEO_DESCRIPTION_EXAMPLE_1}"\n\n'
                f"EXAMPLE 2 ({SEO_DESCRIPTION_EXAMPLE_2_LEN} chars):\n"
                f'"{SEO_DESCRIPTION_EXAMPLE_2}"'
            )),
            xml_block("constraints", (
                "Use only the confirmed facts provided in <context>. "
                f"Do not invent puff counts, shipping promises, nicotine specs, or claims not present in the data. "
                "The description must be plain text — no HTML, no markdown, no line breaks. "
                "Do not echo the accepted seo_title verbatim. "
                f"{m['spelling']} "
                "Return valid JSON only: " + '{"seo_description": "..."}'
                + _market_block
            )),
            xml_block("profile", profile_instructions(prompt_profile, object_type)),
        ])
    if field == "body":
        body_min = BODY_MIN_LENGTH.get(object_type, 300)
        if object_type == "product":
            role = (
                f"You are the senior SEO strategist for {_brand}. "
                "Your task is to write the body HTML content for a single product. "
                "The body must be commercially specific, structured for scannability, "
                "and compliant for the store's market."
                + _brand_voice_block
            )
            constraints = (
                "Use only the confirmed facts in <context>. "
                f"Do not invent specs, puff counts, flavours, shipping claims, or health assertions. "
                "Output valid Shopify-compatible HTML only — no markdown fences, no plain text responses. "
                f"Minimum {body_min} characters. Use question-based H2 or H3 headings. "
                "Internal links: every `<a href>` must use the exact `url` string from `approved_internal_link_targets` in <context> only — never invent paths. "
                + _RAG_INTERNAL_LINK_PREFERENCE
                + "Every `<a>` MUST include a `title` attribute set to the target page's title. "
                "AVOID generic padding links like 'New Arrivals' or 'All Products' at the end of the body — "
                "prefer contextually relevant links from the same collection, brand, or flavour family that appear in `related_content_examples`. "
                "Do not make health, cessation, or medical claims. "
                f"{m['spelling']} "
                "Return valid JSON only."
                + _market_block
            )
        elif object_type == "collection":
            role = (
                f"You are the senior SEO strategist for {_brand}. "
                "Your task is to write the body HTML for a collection (category) page: hub intent, scannable sections, and helpful merchandising context."
            )
            constraints = (
                "Use only the confirmed facts in <context>. "
                "Output valid Shopify-compatible HTML only — no markdown fences, no plain text responses. "
                f"Minimum {body_min} characters. Use H2 or H3 headings for scannability. "
                "Internal links: every `<a href>` must use the exact `url` string from `approved_internal_link_targets` in <context> only — never invent paths. "
                + _RAG_INTERNAL_LINK_PREFERENCE
                + "Every `<a>` MUST include a `title` attribute set to the target page's title. "
                "Do not make health, cessation, or medical claims. "
                "Return valid JSON only."
            )
        elif object_type == "blog_article":
            role = (
                f"You are the senior SEO strategist for {_brand}. "
                "Your task is to write the body HTML for a blog article: editorial depth, scannable structure, and intent-aligned detail (not product-SKU catalog copy)."
            )
            constraints = (
                "Use only the confirmed facts in <context>. "
                "Output valid Shopify-compatible HTML only — no markdown fences, no plain text responses. "
                f"Minimum {body_min} characters. Use H2 or H3 headings where they help readers. "
                "Internal links: every `<a href>` must use the exact `url` string from `approved_internal_link_targets` in <context> only — never invent paths. "
                + _RAG_INTERNAL_LINK_PREFERENCE
                + "Every `<a>` MUST include a `title` attribute set to the target page's title. "
                "Do not make health, cessation, or medical claims. "
                "Return valid JSON only."
            )
        else:
            role = (
                f"You are the senior SEO strategist for {_brand}. "
                "Your task is to write the body HTML for a static/content page: trust, clarity, and intent-aligned detail (not product-SKU copy)."
            )
            constraints = (
                "Use only the confirmed facts in <context>. "
                "Output valid Shopify-compatible HTML only — no markdown fences, no plain text responses. "
                f"Minimum {body_min} characters. Use H2 or H3 headings where they help readers. "
                "Internal links: every `<a href>` must use the exact `url` string from `approved_internal_link_targets` in <context> only — never invent paths. "
                + _RAG_INTERNAL_LINK_PREFERENCE
                + "Every `<a>` MUST include a `title` attribute set to the target page's title. "
                "Do not make health, cessation, or medical claims. "
                "Return valid JSON only."
            )
        return "\n".join([
            xml_block("role", role),
            xml_block("constraints", constraints),
            xml_block("profile", profile_instructions(prompt_profile, object_type)),
        ])
    if field == "tags":
        return "\n".join([
            xml_block("role", (
                f"You are the taxonomy manager for {_brand}. "
                "Your sole task is to write a comma-separated list of taxonomy tags for a single product. "
                "Output nothing except the tags string inside the required JSON object."
            )),
            xml_block("constraints", (
                "Use only the confirmed facts provided in <context>. "
                "Do not invent brands, attributes, or product claims not present in the data. "
                "Tags must be clean, lowercase, and normalized — useful for store filtering and collection logic. "
                "Include: brand, model family, product type, relevant product attributes, and market segment where known. "
                "Return valid JSON only."
            )),
            xml_block("profile", profile_instructions(prompt_profile, object_type)),
        ])
    return "\n".join([
        xml_block("role", f"You are the senior SEO strategist for {_brand}. You are regenerating a single field of an SEO recommendation. The other fields have already been accepted by the operator and must be respected as context."),
        xml_block("constraints", "Use only the provided facts. Do not invent data. Return valid JSON only with the single requested field. Do not contradict or duplicate phrasing from the already-accepted sibling fields."),
        xml_block("profile", profile_instructions(prompt_profile, object_type)),
    ])


def field_user_prompt(
    object_type: str,
    field: str,
    context: dict,
    accepted_fields: dict,
    prompt_version: str,
    *,
    prompt_context_dict: dict | None = None,
    signal_narrative_str: str | None = None,
    retry_feedback: str | None = None,
    conn=None,
) -> str:
    # Extract primary_object from prompt_context_dict if available to avoid redundant computation
    primary_object_from_context = None
    if prompt_context_dict is not None:
        primary_object_from_context = prompt_context_dict.get("primary_object")
    
    if signal_narrative_str is not None:
        signal_narrative = signal_narrative_str
    elif field == "seo_title":
        # Trimmed narrative — body/link sentences are irrelevant to a 65-char title.
        signal_narrative = build_title_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    elif field == "seo_description":
        # Focused narrative — keeps CTR/position; drops body/link/pagespeed.
        signal_narrative = build_description_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    elif field == "tags":
        # Tags are purely taxonomy — performance signals don't help here.
        signal_narrative = ""
    else:
        signal_narrative = build_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    full_context = prompt_context_dict if prompt_context_dict is not None else prompt_context(context)
    slim_context = slim_single_field_prompt_context(object_type, field, full_context)
    instruction = single_field_task_instruction(object_type, field, conn=conn)
    context_json = json.dumps(slim_context, ensure_ascii=True)
    all_siblings_empty = all(not v for v in accepted_fields.values())
    if all_siblings_empty:
        accepted_block = xml_block(
            "accepted_fields",
            f"No sibling fields have been accepted yet. Generate the {field} independently based on product facts and field instructions only.",
        )
    else:
        accepted_json = json.dumps(accepted_fields, ensure_ascii=True)
        accepted_block = xml_block(
            "accepted_fields",
            f"These sibling fields are already accepted. Do not contradict them:\n{accepted_json}",
        )
    sections = []
    # If retry_feedback is provided, put it FIRST so the model sees the retry instruction immediately
    if retry_feedback:
        sections.append(xml_block("retry_instruction", retry_feedback))
    sections.extend([
        xml_block("task", instruction),
        xml_block("signal_narrative", signal_narrative),
        xml_block("field_instructions", single_field_specific_instructions(object_type, field, conn=conn)),
        accepted_block,
        xml_block("formatting", single_field_formatting_instructions(field)),
        xml_block("context", context_json),
    ])
    return "\n\n".join(sections)


def field_review_user_prompt(
    field: str,
    draft_value: str,
    context: dict,
    accepted_fields: dict,
    *,
    prompt_context_dict: dict | None = None,
    signal_narrative_str: str | None = None,
    conn=None,
) -> str:
    m = _market_ctx(conn)
    from .config import get_store_identity
    _store_name, _ = get_store_identity()
    _brand = _store_name or "the store"
    _brand_suffix = f" | {_brand}"
    _brand_suffix_len = len(_brand_suffix)
    # Mirror the same per-field narrative selection used in field_user_prompt so the
    # reviewer operates with the same focused signal as the generator.
    # Extract primary_object from prompt_context_dict if available to avoid redundant computation
    primary_object_from_context = None
    if prompt_context_dict is not None:
        primary_object_from_context = prompt_context_dict.get("primary_object")
    
    if signal_narrative_str is not None:
        signal_narrative = signal_narrative_str
    elif field == "seo_title":
        signal_narrative = build_title_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    elif field == "seo_description":
        signal_narrative = build_description_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    else:
        signal_narrative = build_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    full_context = prompt_context_dict if prompt_context_dict is not None else prompt_context(context)
    # Derive object_type from the context dict — do not hardcode "product" here.
    object_type = full_context.get("object_type") or "product"
    # Use the same slim context for seo_title review as for generation — the reviewer
    # does not need full specs or variant data to approve a 65-char title.
    slim_context = slim_single_field_prompt_context(object_type, field, full_context)
    context_json = json.dumps(slim_context, ensure_ascii=True)
    accepted_json = json.dumps(accepted_fields, ensure_ascii=True)
    if field == "seo_title":
        task_text = (
            "Review this regenerated seo_title against these eight checks:\n"
            f"1. Length: must be 50–65 characters — count every character carefully; reject anything outside this range. Prefer titles closer to 65 characters when additional relevant keywords (e.g., product type, key attribute, '{m['name']}') are available.\n"
            "2. Structure: must lead with Brand + Model + key product descriptor, followed by a differentiating spec or attribute term.\n"
            f"3. Suffix: '{_brand_suffix}' ({_brand_suffix_len} characters including the space and pipe) should be appended if the total stays ≤ 65 characters. If space allows, maximize toward 65 characters by including additional relevant spec terms or '{m['name']}' before the suffix.\n"
            "4. Plain text only: no HTML tags, no markdown, no line breaks, no extra punctuation beyond the single ' | ' separator.\n"
            "5. Facts only: every term must be present in the provided <context> — do not invent specs, flavours, or claims.\n"
            f"6. Geographic signal: '{m['name']}' is desirable when space permits — if the title is under 58 characters without it, suggest adding it before the brand suffix.\n"
            "7. No redundant puff count: if the product name already includes a puff count (e.g. '10000' in 'Draggg 10K'), do NOT repeat it elsewhere in the title (e.g. do NOT write 'Draggg 10K ... 10000 Puffs').\n"
            "8. Spelling: must use Commonwealth English spelling (e.g. 'flavour', 'vapour', 'colour') — reject American spellings like 'flavor', 'vapor', 'color'.\n"
            "Approve if all eight checks pass. Improve or rewrite if any fail, correcting only the specific issue."
        )
    elif field == "seo_description":
        task_text = (
            "Review this regenerated seo_description against these six checks:\n"
            "1. Length: must be 150–160 characters — count every character carefully; reject anything outside this range. "
            "Prefer descriptions closer to 160 characters — short descriptions hurt CTR.\n"
            "2. Hook: must lead with the strongest transactional signal — brand name plus device type or flavour.\n"
            "3. Differentiator: must include the most compelling product differentiator (key spec, unique attribute, or product variety).\n"
            f"4. {m['adjective']} signal: must include a natural buying signal appropriate for a {m['adjective']} online store.\n"
            "5. Complementarity: must not echo the accepted seo_title verbatim — must target secondary intent (use case, spec detail, or buying trigger).\n"
            "6. Spelling: must use Commonwealth English spelling (e.g. 'flavour', 'vapour', 'colour') — reject American spellings.\n"
            "Approve if all six checks pass. Improve or rewrite if any fail, correcting only the specific issue."
        )
    elif field == "body":
        body_min = BODY_MIN_LENGTH.get(object_type, 300)
        if object_type == "product":
            task_text = (
                "Review this regenerated body HTML against these seven checks:\n"
                "1. Length: must be at least 1,500 characters of HTML — count the full string including tags.\n"
                "2. Structure: must follow the five-section order — answer-first opening, flavour profile with H2/H3, specs section, who-it's-for guidance, internal-link mentions.\n"
                "3. Flavour-led: flavour must be the primary merchandising story; specs should be supporting context only.\n"
                "4. Links: every `<a href>` must exactly match a `url` from `approved_internal_link_targets` in <context> — reject invented URLs. "
                "Avoid generic footer links like 'New Arrivals' or 'All Products' when better same-collection or category links exist in `related_content_examples`.\n"
                "5. Compliance: no health claims, no cessation claims, no invented product specs or shipping promises.\n"
                "6. Spelling: must use Commonwealth English spelling throughout (e.g. 'flavour', 'vapour', 'colour', 'favourite') — reject American spellings.\n"
                "7. No redundant puff count: if the product name includes a puff count (e.g. '10000'), do not repeat it redundantly in headings or body copy.\n"
                "Approve if all seven checks pass. Improve or rewrite if any fail, correcting only the specific issue."
            )
        elif object_type == "collection":
            task_text = (
                f"Review this regenerated body HTML against these five checks:\n"
                f"1. Length: must be at least {body_min} characters of HTML — count the full string including tags.\n"
                "2. Structure: scannable sections with H2 or H3 headings; clear category/hub intent.\n"
                "3. Merchandising: copy should fit this collection specifically — not generic filler.\n"
                "4. Links: every `<a href>` must exactly match a `url` from `approved_internal_link_targets` in <context> — reject invented URLs.\n"
                "5. Compliance: no health claims, no cessation claims, no invented specs or shipping promises.\n"
                "Approve if all five checks pass. Improve or rewrite if any fail, correcting only the specific issue."
            )
        else:
            task_text = (
                f"Review this regenerated body HTML against these five checks:\n"
                f"1. Length: must be at least {body_min} characters of HTML — count the full string including tags.\n"
                "2. Structure: scannable sections with H2 or H3 where appropriate; aligned to this page's purpose.\n"
                "3. Specificity: copy should fit this page's intent — not generic product or category boilerplate.\n"
                "4. Links: every `<a href>` must exactly match a `url` from `approved_internal_link_targets` in <context> — reject invented URLs.\n"
                "5. Compliance: no health claims, no cessation claims, no invented facts.\n"
                "Approve if all five checks pass. Improve or rewrite if any fail, correcting only the specific issue."
            )
    else:
        task_text = (
            f"Review this single regenerated '{field}' value. "
            "Approve it as-is, improve it with targeted edits, or rewrite it. "
            "Consider the already-accepted sibling fields for consistency."
        )
    sections = [
        xml_block("task", task_text),
        xml_block("signal_narrative", signal_narrative),
        xml_block("accepted_fields", accepted_json),
        xml_block("draft_value", draft_value),
        xml_block("formatting", f"Return one JSON object with key '{field}' (the final value) and key '_review' (an object with one entry: {{'{field}': 'approved'|'improved'|'rewritten'}})."),
        xml_block("context", context_json),
    ]
    return "\n\n".join(sections)


def schema(object_type: str) -> dict:
    base = {"seo_title": "string", "seo_description": "string", "body": "string", "why": "array", "priority_actions": "array", "internal_links": "array"}
    if object_type == "product":
        base["tags"] = "string"
    return base


def user_prompt(
    object_type: str,
    context: dict,
    prompt_version: str,
    *,
    prompt_context_dict: dict | None = None,
    signal_narrative_str: str | None = None,
    conn=None,
) -> str:
    m = _market_ctx(conn)
    field_list = ["seo_title", "seo_description", "body", "why", "priority_actions", "internal_links"]
    if object_type == "product":
        field_list.insert(3, "tags")
    full_context = prompt_context_dict if prompt_context_dict is not None else prompt_context(context)
    # Extract primary_object from prompt_context_dict if available to avoid redundant computation
    primary_object_from_context = None
    if prompt_context_dict is not None:
        primary_object_from_context = prompt_context_dict.get("primary_object")
    signal_narrative = signal_narrative_str if signal_narrative_str is not None else build_signal_narrative(context, primary_object=primary_object_from_context, conn=conn)
    if object_type == "product":
        object_rules = xml_block("field_instructions", object_field_instructions(object_type, conn=conn))
    elif object_type == "collection":
        object_rules = xml_block("field_instructions", object_field_instructions(object_type, conn=conn))
    else:
        object_rules = xml_block("field_instructions", object_field_instructions(object_type, conn=conn))
    recommendation_standards = xml_block("recommendation_standards", f"Prompt version: {prompt_version}\nObject type: {object_type}\nUse all available evidence, not generic best practices. Prioritize the highest-likelihood ranking improvements for this specific URL. Prefer exact commercial phrasing used by {m['adjective']} shoppers. Optimize for manual Shopify editing: concise metadata, clear body structure, usable tags, and actionable links. Keep the copy compliant for an store in {m['name']}. If query-level GSC data exists, use it to sharpen title and meta targeting. If `gsc_segment_summary` (device/country/search-appearance splits) or `segment_query_keywords` (query×segment rows) appears in context, treat both as supporting evidence only — cite listed buckets/queries and shares/counts, and never invent segment breakdowns. If those signals are missing, still produce the strongest specific recommendation from confirmed facts, catalog relationships, and intent.")
    anti_sameness_body = {
        "product": "Do not write body copy that could fit any generic product page.",
        "collection": "Do not write body copy that could fit any generic category page without this collection's specific intent.",
        "page": "Do not write body copy that could fit any generic store page without this page's specific purpose and facts.",
    }.get(object_type, "Avoid generic, interchangeable body copy.")
    anti_sameness = xml_block(
        "anti_sameness",
        "Do not reuse opening sentence structure, title phrasing, or meta hooks from recommendation history. Do not stuff keywords. "
        f"Do not repeat the same model or flavor phrase awkwardly. Do not overuse {m['name']} unnaturally. Do not recommend vague or duplicate tags. "
        + anti_sameness_body,
    )
    evidence_rules = xml_block("evidence_rules", f"High impressions + low CTR -> prioritize title/meta rewrite.\nPosition 8-20 -> prioritize stronger commercial alignment, content depth, and internal links.\nIndexed but weak engagement -> improve on-page clarity and match search intent better.\nHigh per-URL views but weak search visibility -> strengthen SEO targeting and internal links.\nThin or overlapping catalog copy -> produce stronger differentiated body recommendations.\nImportant object with weak internal links -> recommend explicit internal links.\nWeak PageSpeed or indexing issues should influence the rationale and priority actions.\n{version_specific_guidance(prompt_version, object_type).strip()}")
    formatting = xml_block("formatting", formatting_instructions(object_type, field_list))
    return "\n\n".join([recommendation_standards, xml_block("signal_narrative", signal_narrative), object_rules, anti_sameness, evidence_rules, formatting, xml_block("context", json.dumps(full_context, ensure_ascii=True))])
