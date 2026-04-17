import { useEffect, useState } from "react";

export type DashboardGscPeriod = "mtd" | "full_months";

const STORAGE_KEY = "shopifyseo_dashboard_gsc_period";
export const DASHBOARD_GSC_PERIOD_EVENT = "shopifyseo-dashboard-gsc-period";

export function readStoredGscPeriod(): DashboardGscPeriod {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "full_months" || v === "mtd") return v;
  } catch {
    /* ignore */
  }
  return "mtd";
}

export function persistDashboardGscPeriod(period: DashboardGscPeriod): void {
  try {
    localStorage.setItem(STORAGE_KEY, period);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new CustomEvent<DashboardGscPeriod>(DASHBOARD_GSC_PERIOD_EVENT, { detail: period }));
}

/** Subscribes to Overview and same-tab period changes so detail queries refetch with the matching window. */
export function useDashboardGscPeriodSync(): DashboardGscPeriod {
  const [period, setPeriod] = useState<DashboardGscPeriod>(readStoredGscPeriod);
  useEffect(() => {
    const onCustom = (e: Event) => {
      const ce = e as CustomEvent<DashboardGscPeriod>;
      if (ce.detail === "mtd" || ce.detail === "full_months") setPeriod(ce.detail);
    };
    window.addEventListener(DASHBOARD_GSC_PERIOD_EVENT, onCustom);
    return () => window.removeEventListener(DASHBOARD_GSC_PERIOD_EVENT, onCustom);
  }, []);
  return period;
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
