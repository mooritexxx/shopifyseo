import html
from datetime import datetime


def escape_value(value: str) -> str:
    return html.escape(value or "")


def cache_status_label(meta: dict | None) -> str:
    if meta and meta.get("rate_limited"):
        return "Rate limited"
    if not meta or not meta.get("exists"):
        return "Never fetched"
    return "Stale" if meta.get("stale") else "Fresh"


def cache_status_kind(meta: dict | None) -> str:
    if meta and meta.get("rate_limited"):
        return "medium"
    if not meta or not meta.get("exists"):
        return "medium"
    return "medium" if meta.get("stale") else "low"


def cache_status_text(meta: dict | None) -> str:
    if meta and meta.get("rate_limited"):
        return "PageSpeed API rate limited. Retry later."
    if not meta or not meta.get("exists"):
        return "Never fetched"
    fetched_at = meta.get("fetched_at")
    if not fetched_at:
        return cache_status_label(meta)
    try:
        stamp = datetime.fromtimestamp(int(fetched_at)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        stamp = str(fetched_at)
    prefix = "Stale" if meta.get("stale") else "Fresh"
    return f"{prefix} · fetched {stamp}"


_INDEX_NEGATIVE_MARKERS = (
    "not indexed",
    "excluded",
    "blocked",
    "error",
    "duplicate",
    "discovered - currently not indexed",
    "crawled - currently not indexed",
    "soft 404",
    "unknown to google",
)


def index_status_bucket_from_strings(indexing: str, coverage: str) -> str:
    """Classify indexingState / coverageState or stored index panel labels into a rollup bucket."""
    indexing = (indexing or "").strip()
    coverage = (coverage or "").strip()
    if not indexing and not coverage:
        return "unknown"
    lab = indexing.lower()
    if lab == "unknown":
        return "unknown"
    if lab == "indexed":
        return "indexed"
    if lab == "not indexed":
        return "not_indexed"
    if lab == "needs review":
        return "needs_review"
    raw = f"{indexing} {coverage}".strip().lower()
    if any(marker in raw for marker in _INDEX_NEGATIVE_MARKERS):
        return "not_indexed"
    if "indexed" in raw:
        return "indexed"
    return "needs_review"


_INDEX_ROW_FIELDS = ("index_status", "index_coverage", "google_canonical")


def _has_index_data(row: dict) -> bool:
    """Return True if any denormalized index field is populated in the catalog row."""
    return any(str(row.get(f) or "").strip() for f in _INDEX_ROW_FIELDS)


def inspection_for_catalog_index_display(
    inspection_detail: dict | None, summary_row: dict | None
) -> dict | None:
    """Build the inspection-shaped dict used for Index labels on catalog list vs detail.

    When the catalog row has denormalized index fields (same source as list tables), use **only**
    those values so product/collection/page/article detail matches the Status column. Otherwise
    fall back to cached URL Inspection API payload (e.g. before any index sync wrote columns).
    """
    row = summary_row or {}
    if _has_index_data(row):
        return {
            "inspectionResult": {
                "indexStatusResult": {
                    "indexingState": str(row.get("index_status") or ""),
                    "coverageState": str(row.get("index_coverage") or ""),
                    "googleCanonical": str(row.get("google_canonical") or ""),
                }
            }
        }
    return inspection_detail


def index_status_info(inspection_detail: dict | None) -> tuple[str, str, str]:
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    indexing = (idx.get("indexingState") or "").strip()
    coverage = (idx.get("coverageState") or "").strip()
    bucket = index_status_bucket_from_strings(indexing, coverage)
    if bucket == "unknown":
        return "Unknown", "medium", "No inspection data"
    if bucket == "not_indexed":
        return "Not Indexed", "high", indexing or coverage
    if bucket == "indexed":
        return "Indexed", "low", indexing or coverage
    return "Needs Review", "medium", indexing or coverage or "Inspection available"
