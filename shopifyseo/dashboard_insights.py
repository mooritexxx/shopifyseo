from urllib.parse import urlparse


def path_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    return parsed.path or "/"


def gsc_row_for_url(summary: dict | None, url: str) -> dict | None:
    if not summary:
        return None
    for row in summary.get("pages", []):
        if ((row.get("keys") or [""])[0]) == url:
            return row
    return None


def ga4_row_for_url(summary: dict | None, url: str) -> dict | None:
    if not summary:
        return None
    path = path_from_url(url)
    for row in summary.get("rows", []):
        values = row.get("dimensionValues") or [{"value": ""}]
        if values[0].get("value", "") == path:
            return row
    return None


def inspection_summary(payload: dict | None) -> dict:
    if not payload:
        return {}
    return payload.get("inspectionResult", {}).get("indexStatusResult", {}) or {}


def opportunity_priority(score: int) -> str:
    if score >= 55:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


def blended_opportunity(
    *,
    base_score: int,
    url: str,
    competitors: list[dict] | None = None,
    gsc_summary: dict | None = None,
    ga4_summary: dict | None = None,
    inspection: dict | None = None,
) -> dict:
    score = max(0, 100 - int(base_score))
    reasons: list[str] = []

    gsc_row = gsc_row_for_url(gsc_summary, url)
    if gsc_row:
        impressions = int(gsc_row.get("impressions", 0))
        ctr = float(gsc_row.get("ctr", 0) or 0)
        position = float(gsc_row.get("position", 0) or 0)
        if impressions >= 20:
            score += min(20, impressions // 10)
            reasons.append("real search demand")
        if impressions >= 20 and ctr < 0.04:
            score += 12
            reasons.append("weak CTR")
        if 8 < position <= 20:
            score += 12
            reasons.append("near page 1")

    ga4_row = ga4_row_for_url(ga4_summary, url)
    if ga4_row:
        metrics = ga4_row.get("metricValues", [])
        sessions = int(float(metrics[0].get("value", 0))) if len(metrics) > 0 else 0
        avg_duration = float(metrics[2].get("value", 0)) if len(metrics) > 2 else 0.0
        if sessions >= 5:
            score += min(12, sessions // 2)
            reasons.append("organic landing traffic")
        if sessions >= 5 and avg_duration < 60:
            score += 10
            reasons.append("weak engagement")

    idx = inspection_summary(inspection)
    indexing_state = (idx.get("indexingState") or "").lower()
    coverage_state = (idx.get("coverageState") or "").lower()
    google_canonical = idx.get("googleCanonical") or ""
    if indexing_state and "indexed" not in indexing_state:
        score += 18
        reasons.append("not indexed")
    if coverage_state and ("error" in coverage_state or "excluded" in coverage_state):
        score += 12
        reasons.append("coverage issue")
    if google_canonical and google_canonical != url:
        score += 8
        reasons.append("canonical mismatch")

    competitor_count = len(competitors or [])
    if competitor_count:
        score += min(15, competitor_count * 3)
        reasons.append("competitor pressure")

    score = min(score, 100)
    return {
        "score": score,
        "priority": opportunity_priority(score),
        "reasons": reasons,
        "gsc_row": gsc_row,
        "ga4_row": ga4_row,
        "inspection": idx,
    }
