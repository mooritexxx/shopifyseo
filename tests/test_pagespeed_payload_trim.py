"""PageSpeed payloads keep every number and drop only presentation data."""

from shopifyseo.dashboard_google._gsc import trim_pagespeed_payload
from shopifyseo.dashboard_store import _pagespeed_denormalized_fields


def _psi_payload() -> dict:
    return {
        "id": "https://x/p",
        "analysisUTCTimestamp": "2026-07-25T00:00:00Z",
        "loadingExperience": {"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2100}}},
        "originLoadingExperience": {"metrics": {}},
        "lighthouseResult": {
            "requestedUrl": "https://x/p",
            "finalUrl": "https://x/p",
            "fetchTime": "2026-07-25T00:00:00Z",
            "lighthouseVersion": "11.0.0",
            "configSettings": {"formFactor": "mobile"},
            "categories": {
                "performance": {"score": 0.83, "auditRefs": [{"id": "lcp", "weight": 25}]},
                "seo": {"score": 0.92},
            },
            "audits": {
                "largest-contentful-paint": {
                    "id": "largest-contentful-paint",
                    "title": "Largest Contentful Paint",
                    "score": 0.71,
                    "numericValue": 2530.5,
                    "numericUnit": "millisecond",
                    "displayValue": "2.5 s",
                    "details": {"type": "table", "items": [{"junk": "x" * 20000}]},
                },
                "full-page-screenshot": {"details": {"screenshot": {"data": "y" * 200000}}},
            },
            "i18n": {"rendererFormattedStrings": {"a": "b" * 5000}},
            "timing": {"total": 1234.5},
        },
    }


def test_scores_the_catalog_reads_survive() -> None:
    trimmed = trim_pagespeed_payload(_psi_payload())
    perf, seo, _status, _ts = _pagespeed_denormalized_fields({**trimmed, "_cache": {"exists": True}})
    assert perf == 83
    assert seo == 92


def test_every_audit_number_is_kept() -> None:
    audit = trim_pagespeed_payload(_psi_payload())["lighthouseResult"]["audits"]["largest-contentful-paint"]
    assert audit["numericValue"] == 2530.5
    assert audit["numericUnit"] == "millisecond"
    assert audit["displayValue"] == "2.5 s"
    assert audit["score"] == 0.71


def test_presentation_payload_is_dropped() -> None:
    lh = trim_pagespeed_payload(_psi_payload())["lighthouseResult"]
    assert "details" not in lh["audits"]["largest-contentful-paint"]
    assert "full-page-screenshot" not in lh["audits"]
    assert "i18n" not in lh
    assert "timing" not in lh


def test_field_data_and_top_level_keys_survive() -> None:
    trimmed = trim_pagespeed_payload(_psi_payload())
    assert trimmed["loadingExperience"]["metrics"]["LARGEST_CONTENTFUL_PAINT_MS"]["percentile"] == 2100
    assert trimmed["id"] == "https://x/p"
    assert trimmed["analysisUTCTimestamp"] == "2026-07-25T00:00:00Z"
    assert trimmed["lighthouseResult"]["configSettings"]["formFactor"] == "mobile"


def test_size_reduction_is_substantial() -> None:
    import json

    full = _psi_payload()
    before = len(json.dumps(full))
    after = len(json.dumps(trim_pagespeed_payload(full)))
    assert after < before / 20, f"expected a large reduction, got {before} -> {after}"


def test_rate_limited_placeholder_passes_through() -> None:
    """The 429 path writes a marker payload with no lighthouseResult."""
    payload = {"_error": {"status": 429}, "_meta": {"rate_limited": True, "retry_after_at": 123}}
    assert trim_pagespeed_payload(payload) == payload


def test_malformed_payloads_do_not_raise() -> None:
    assert trim_pagespeed_payload({}) == {}
    assert trim_pagespeed_payload({"lighthouseResult": None}) == {"lighthouseResult": None}
    assert trim_pagespeed_payload({"lighthouseResult": {"audits": "nonsense"}})["lighthouseResult"] == {}
