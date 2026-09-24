/**
 * Per-URL Search Console numbers are not period-selectable.
 *
 * Catalog rows and detail pages both read what the sync stored, and the sync writes
 * exactly one window (`GSC_CATALOG_PERIOD_MODE` on the backend — a rolling 30 days).
 * Letting the UI request a different window produced pages with no data at all, because
 * no cache row exists for it. The Overview helpers below are separate: those aggregates
 * are fetched live, so they genuinely can be re-windowed.
 */
export function catalogGscWindowDescription(): string {
  return "Last 30 days — same Search Console window as the GSC signal cards above.";
}

/** Overview-only: rolling 30d (default) vs full property history since 2026-02-15. */
export type OverviewGscPeriod = "rolling_30d" | "since_2026_02_15";

const OVERVIEW_GSC_STORAGE_KEY = "shopifyseo_overview_gsc_period";

export function readStoredOverviewGscPeriod(): OverviewGscPeriod {
  try {
    const v = localStorage.getItem(OVERVIEW_GSC_STORAGE_KEY);
    if (v === "since_2026_02_15" || v === "rolling_30d") return v;
  } catch {
    /* ignore */
  }
  return "rolling_30d";
}

export function persistOverviewGscPeriod(period: OverviewGscPeriod): void {
  try {
    localStorage.setItem(OVERVIEW_GSC_STORAGE_KEY, period);
  } catch {
    /* ignore */
  }
}
