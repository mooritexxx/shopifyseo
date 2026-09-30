"""Shared, configurable metadata policy for AI, review and editor saves."""
from .dashboard_ai_engine_parts.config import (
    TITLE_HARD_MIN, TITLE_TARGET_MIN, TITLE_LIMIT,
    DESCRIPTION_HARD_MIN, DESCRIPTION_TARGET_MIN, DESCRIPTION_LIMIT,
)


def quality_policy(kind):
    return {
        'seo_title': {'minimum': TITLE_HARD_MIN.get(kind, 40), 'target': TITLE_TARGET_MIN.get(kind, 45), 'maximum': TITLE_LIMIT},
        'seo_description': {'minimum': DESCRIPTION_HARD_MIN.get(kind, 110), 'target': DESCRIPTION_TARGET_MIN.get(kind, 145), 'maximum': DESCRIPTION_LIMIT},
    }


def metadata_issues(kind, fields):
    issues = []
    for field, rule in quality_policy(kind).items():
        if field not in fields:
            continue
        length = len(str(fields[field] or '').strip())
        label = field.replace('_', ' ').capitalize()
        if length < rule['minimum'] or length > rule['maximum']:
            issues.append({'field': field, 'severity': 'error', 'message': f"{label} too {'short' if length < rule['minimum'] else 'long'} ({length} characters; required {rule['minimum']}–{rule['maximum']})."})
        elif length < rule['target']:
            issues.append({'field': field, 'severity': 'warning', 'message': f"{label}: {length} characters; recommended {rule['target']}–{rule['maximum']}."})
    return issues


def validate_metadata(kind, fields):
    errors = [i['message'] for i in metadata_issues(kind, fields) if i['severity'] == 'error']
    if errors:
        raise ValueError(' '.join(errors))


def validate_changed_metadata(kind, current, payload):
    # Existing content and intentional clearing remain editable; enforce nonempty changes.
    changed = {k: v for k, v in payload.items() if k in quality_policy(kind) and str(v or '').strip() and str(v) != str(current.get(k) or '')}
    validate_metadata(kind, changed)
