"""Article body HTML helpers: first paragraph, H2 sections, image injection."""

import html
import re


def extract_first_paragraph_plain_text(body_html: str, *, max_chars: int = 520) -> str:
    """Plain text from the first <p>…</p> block (for image prompts tied to the intro section)."""
    m = re.search(r"<p\b[^>]*>(.*?)</p>", body_html or "", re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    inner = m.group(1)
    inner = re.sub(r"<[^>]+>", " ", inner)
    inner = html.unescape(inner)
    inner = re.sub(r"\s+", " ", inner).strip()
    if not inner:
        return ""
    return inner[:max_chars]


# ---------------------------------------------------------------------------
# H2 section parsing — used to generate one image per major section
# ---------------------------------------------------------------------------

def _strip_html_tags(text: str) -> str:
    """Remove HTML tags and unescape entities to plain text."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_h2_sections(body_html: str) -> list[dict]:
    """Parse article body HTML into H2-delimited sections.

    Returns a list of dicts, each with:
        heading     – plain text of the H2
        context     – first ~500 chars of plain text from paragraphs in that section
        insert_pos  – character index in *body_html* after the first </p> within the section
                      (where a section image should be injected)
    Sections before the first H2 (intro) are excluded — they already get the featured/intro image.
    """
    h2_pattern = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
    p_pattern = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)

    matches = list(h2_pattern.finditer(body_html))
    if not matches:
        return []

    sections = []
    for i, m in enumerate(matches):
        heading = _strip_html_tags(m.group(1))
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(body_html)
        section_html = body_html[section_start:section_end]

        # Collect plain text from <p> tags within this section (up to 500 chars)
        paragraphs = p_pattern.findall(section_html)
        context_parts: list[str] = []
        total = 0
        for p_inner in paragraphs:
            plain = _strip_html_tags(p_inner)
            if plain:
                context_parts.append(plain)
                total += len(plain)
                if total >= 500:
                    break
        context = " ".join(context_parts)[:500]

        # Insertion point: after the first </p> in the section
        p_end_offset = section_html.lower().find("</p>")
        if p_end_offset != -1:
            insert_pos = section_start + p_end_offset + 4  # len("</p>") == 4
        else:
            insert_pos = section_start

        sections.append({"heading": heading, "context": context, "insert_pos": insert_pos})

    return sections


def inject_article_body_image(body_html: str, image_url: str, alt_text: str) -> str:
    """Insert a hero <img> after the first </p> (intro). Uses a simple <p><img></p> — Shopify's editor often strips <figure>."""
    url = (image_url or "").strip()
    if not url.startswith("https://"):
        return body_html
    alt = (alt_text or "").strip() or "Blog hero image"
    safe_alt = html.escape(alt, quote=True)
    block = f'<p><img src="{url}" alt="{safe_alt}" loading="lazy" /></p>'
    lower = body_html.lower()
    idx = lower.find("</p>")
    if idx == -1:
        return block + "\n" + body_html
    end = idx + 4
    return body_html[:end] + "\n" + block + "\n" + body_html[end:]


def inject_article_body_images(body_html: str, images: list[dict]) -> str:
    """Inject multiple images into an article body at their designated positions.

    Each entry in *images* must have keys: url, alt, insert_pos (char index in body_html).
    Images are inserted bottom-up so earlier insertion positions stay valid.
    """
    valid = [img for img in images if (img.get("url") or "").strip().startswith("https://")]
    if not valid:
        return body_html
    # Sort descending by insert_pos so we inject from bottom to top
    for img in sorted(valid, key=lambda x: x["insert_pos"], reverse=True):
        alt = (img.get("alt") or "").strip() or "Blog article image"
        safe_alt = html.escape(alt, quote=True)
        block = f'\n<p><img src="{img["url"]}" alt="{safe_alt}" loading="lazy" /></p>\n'
        pos = img["insert_pos"]
        body_html = body_html[:pos] + block + body_html[pos:]
    return body_html
