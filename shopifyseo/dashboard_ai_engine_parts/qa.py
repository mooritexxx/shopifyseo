"""QA validation for AI-generated SEO content."""
from __future__ import annotations

from .config import (
    BODY_MIN_LENGTH,
    DESCRIPTION_HARD_MIN,
    DESCRIPTION_LIMIT,
    DESCRIPTION_TARGET_MIN,
    QA_SCORE_FLOOR,
    TITLE_HARD_MIN,
    TITLE_LIMIT,
    TITLE_TARGET_MIN,
)


class RecommendationValidationError(ValueError):
    """Raised when generated content fails hard validation constraints."""


def _clamp_text_to_max_length(text: str, max_len: int) -> str:
    """Trim to max_len, preferring a word boundary when it preserves most of the budget."""
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    last_space = cut.rfind(" ")
    if last_space >= int(max_len * 0.55):
        cut = cut[:last_space]
    out = cut.rstrip()
    if not out:
        return s[:max_len].rstrip()
    # Avoid an aggressive word trim that drops far below the max (would fail min-length checks).
    if len(out) < max_len - 20:
        return s[:max_len].rstrip()
    return out


def _strip_trailing_pipe(text: str) -> str:
    """Remove a dangling ' | ' or ' |' left when the brand suffix is empty or truncated."""
    import re
    return re.sub(r"\s*\|\s*$", "", text)


def clamp_generated_seo_field(field: str, value: str) -> str:
    """Ensure AI/meta outputs never exceed storage/UI limits (providers may ignore JSON maxLength)."""
    cleaned = _strip_trailing_pipe((value or "").strip())
    if field == "seo_title":
        return _clamp_text_to_max_length(cleaned, TITLE_LIMIT)
    if field == "seo_description":
        return _clamp_text_to_max_length(cleaned, DESCRIPTION_LIMIT)
    return cleaned if isinstance(value, str) else ""


def _score_title(object_type: str, title: str) -> tuple[float, list[str]]:
    issues: list[str] = []
    length = len(title.strip())
    hard_min = TITLE_HARD_MIN.get(object_type, 40)
    target_min = TITLE_TARGET_MIN.get(object_type, 45)

    if length < hard_min:
        issues.append(f"SEO title too short ({length} chars, minimum {hard_min})")
    elif length < target_min:
        issues.append(f"SEO title below target length ({length} chars, target {target_min}+)")
    if length > TITLE_LIMIT:
        issues.append(f"SEO title too long ({length} chars, max {TITLE_LIMIT})")

    score = 1.0
    if length < hard_min:
        score = 0.2
    elif length < target_min:
        score = 0.7
    elif length > TITLE_LIMIT:
        score = 0.8
    return score, issues


def _score_description(object_type: str, description: str) -> tuple[float, list[str]]:
    issues: list[str] = []
    length = len(description.strip())
    hard_min = DESCRIPTION_HARD_MIN.get(object_type, 110)
    target_min = DESCRIPTION_TARGET_MIN.get(object_type, 135)

    if length < hard_min:
        issues.append(f"Meta description too short ({length} chars, minimum {hard_min})")
    elif length < target_min:
        issues.append(f"Meta description below target ({length} chars, target {target_min}+)")
    if length > DESCRIPTION_LIMIT:
        issues.append(f"Meta description too long ({length} chars, max {DESCRIPTION_LIMIT})")

    score = 1.0
    if length < hard_min:
        score = 0.2
    elif length < target_min:
        score = 0.7
    elif length > DESCRIPTION_LIMIT:
        score = 0.8
    return score, issues


def _score_body(object_type: str, body: str) -> tuple[float, list[str]]:
    issues: list[str] = []
    # Strip basic HTML tags for length measurement
    import re
    text = re.sub(r"<[^>]+>", "", body or "")
    length = len(text.strip())
    min_length = BODY_MIN_LENGTH.get(object_type, 300)

    if length < min_length:
        issues.append(f"Body content too short ({length} chars, minimum {min_length})")

    score = 1.0 if length >= min_length else max(0.1, length / min_length)
    return score, issues


def validate_output(
    object_type: str,
    output: dict,
    *,
    floor: float | None = None,
) -> tuple[float, list[str]]:
    """Validate a full recommendation output dict. Returns (score, issues).

    Score is 0.0-1.0; floor defaults to QA_SCORE_FLOOR[object_type] / 10.
    """
    all_issues: list[str] = []
    scores: list[float] = []

    seo_title = (output.get("seo_title") or "").strip()
    seo_description = (output.get("seo_description") or "").strip()
    body = (output.get("body") or output.get("body_html") or "").strip()

    if seo_title:
        s, issues = _score_title(object_type, seo_title)
        scores.append(s)
        all_issues.extend(issues)

    if seo_description:
        s, issues = _score_description(object_type, seo_description)
        scores.append(s)
        all_issues.extend(issues)

    if body:
        s, issues = _score_body(object_type, body)
        scores.append(s)
        all_issues.extend(issues)

    overall = sum(scores) / len(scores) if scores else 1.0

    if floor is None:
        floor = QA_SCORE_FLOOR.get(object_type, 4) / 10.0

    return overall, all_issues


def validate_single_field(
    object_type: str,
    field: str,
    value: str,
    context: dict | None = None,
) -> None:
    """Validate a single generated field. Raises RecommendationValidationError on hard failure."""
    value = (value or "").strip()
    if not value:
        raise RecommendationValidationError(f"Generated {field} is empty")

    if field == "seo_title":
        hard_min = TITLE_HARD_MIN.get(object_type, 40)
        if len(value) < hard_min:
            raise RecommendationValidationError(
                f"Generated seo_title too short: {len(value)} chars (minimum {hard_min})"
            )
        if len(value) > TITLE_LIMIT:
            raise RecommendationValidationError(
                f"Generated seo_title too long: {len(value)} chars (maximum {TITLE_LIMIT})"
            )

    elif field == "seo_description":
        hard_min = DESCRIPTION_HARD_MIN.get(object_type, 110)
        if len(value) < hard_min:
            raise RecommendationValidationError(
                f"Generated seo_description too short: {len(value)} chars (minimum {hard_min})"
            )
        if len(value) > DESCRIPTION_LIMIT:
            raise RecommendationValidationError(
                f"Generated seo_description too long: {len(value)} chars (maximum {DESCRIPTION_LIMIT})"
            )

    elif field == "body":
        import re
        text = re.sub(r"<[^>]+>", "", value)
        min_length = BODY_MIN_LENGTH.get(object_type, 300)
        if len(text.strip()) < min_length // 2:
            raise RecommendationValidationError(
                f"Generated body too short: {len(text.strip())} chars (minimum {min_length // 2})"
            )


def build_retry_feedback(
    object_type: str,
    field: str,
    value: str,
    issues: list[str],
) -> str:
    """Build a feedback message for retrying a failed generation."""
    lines = [f"The previous {field} generation had issues that must be fixed:"]
    for issue in issues:
        lines.append(f"  - {issue}")

    if field == "seo_title":
        target_min = TITLE_TARGET_MIN.get(object_type, 45)
        lines.append(f"\nRequirements: {target_min}-{TITLE_LIMIT} characters. Do not truncate with '...'.")
    elif field == "seo_description":
        target_min = DESCRIPTION_TARGET_MIN.get(object_type, 135)
        lines.append(f"\nRequirements: {target_min}-{DESCRIPTION_LIMIT} characters. Fill the space with relevant detail.")
    elif field == "body":
        min_length = BODY_MIN_LENGTH.get(object_type, 300)
        lines.append(f"\nRequirements: minimum {min_length} characters of meaningful content.")

    lines.append(f"\nPrevious output was: {value[:200]!r}{'...' if len(value) > 200 else ''}")
    return "\n".join(lines)


def build_retry_feedback_from_error(
    field: str,
    error: Exception,
    value: str,
) -> str:
    """Build feedback from a validation exception."""
    return (
        f"The previous {field} generation failed validation: {error}\n"
        f"Previous output was: {value[:200]!r}{'...' if len(value) > 200 else ''}\n"
        f"Please regenerate with corrections."
    )


def _normalize_spec_value(value: str | None) -> set[str]:
    """Extract all numeric values from a spec string (handles ranges like '20mg/50mg')."""
    if not value:
        return set()
    import re
    value_str = str(value).lower().strip()
    numbers = re.findall(r"\d+(?:\.\d+)?", value_str)
    return set(numbers)


def validate_body_spec_claims(
    body: str,
    product_specs: dict,
) -> tuple[bool, list[str]]:
    """Validate that numeric spec claims in body are present in product_specs.

    Checks for puff count, nicotine strength, and battery size/capacity claims.
    Returns (passed, issues).
    """
    import re
    issues: list[str] = []
    if not body or not product_specs:
        return True, issues

    text = re.sub(r"<[^>]+>", " ", body).lower()

    # Extract allowed values from product_specs
    allowed_puff = _normalize_spec_value(product_specs.get("puff_count"))
    allowed_nicotine = _normalize_spec_value(product_specs.get("nicotine_strength"))
    allowed_battery = _normalize_spec_value(product_specs.get("battery_size"))

    # Patterns to match spec claims in body text
    # Puff count patterns: "800 puffs", "8000-puff", "delivers 10000 puffs"
    puff_patterns = [
        r"(\d+)\s*[-–]?\s*puff",
        r"(\d+)\s+puffs?\b",
        r"delivers\s+(\d+)",
        r"up\s+to\s+(\d+)\s+puffs?",
    ]
    # Nicotine patterns: "20mg", "20 mg", "50mg/ml", "2% nicotine"
    nicotine_patterns = [
        r"(\d+(?:\.\d+)?)\s*mg(?:\s*/\s*ml)?(?:\s+nicotine)?",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:nicotine)?",
        r"nicotine\s*(?:strength|level)?\s*(?:of|:)?\s*(\d+(?:\.\d+)?)",
    ]
    # Battery patterns: "800mAh", "800 mAh", "battery capacity"
    battery_patterns = [
        r"(\d+)\s*mah",
        r"battery\s*(?:capacity|size)?\s*(?:of|:)?\s*(\d+)",
    ]

    # Check puff count claims
    if allowed_puff:
        for pattern in puff_patterns:
            for match in re.finditer(pattern, text):
                claimed = match.group(1)
                if claimed and claimed not in allowed_puff:
                    issues.append(
                        f"Body claims '{claimed}' puffs but product_specs.puff_count is '{product_specs.get('puff_count')}'"
                    )

    # Check nicotine claims
    if allowed_nicotine:
        for pattern in nicotine_patterns:
            for match in re.finditer(pattern, text):
                claimed = match.group(1)
                if claimed and claimed not in allowed_nicotine:
                    issues.append(
                        f"Body claims '{claimed}' nicotine but product_specs.nicotine_strength is '{product_specs.get('nicotine_strength')}'"
                    )

    # Check battery claims
    if allowed_battery:
        for pattern in battery_patterns:
            for match in re.finditer(pattern, text):
                # Some patterns have the number in group 1 or 2
                claimed = match.group(1) if match.group(1) else (match.group(2) if match.lastindex >= 2 else None)
                if claimed and claimed not in allowed_battery:
                    issues.append(
                        f"Body claims '{claimed}' battery but product_specs.battery_size is '{product_specs.get('battery_size')}'"
                    )

    passed = len(issues) == 0
    return passed, issues
