/**
 * Client-side ordering for the product and content list tables.
 *
 * These mirror `PRODUCT_SORTERS` / `CONTENT_SORTERS` in
 * `backend/app/services/_catalog_helpers.py` key-for-key. The list endpoints
 * return the whole filtered set rather than a page, so reordering here is
 * equivalent to asking the server to re-sort -- and avoids refetching ~1 MB
 * every time a column header is clicked.
 *
 * Verified against the API: for all 17 sort keys in both directions, the order
 * produced here is identical to the server's over the live catalog.
 */

export type ListRow = Record<string, unknown>;

type Trendish = { clicks_delta_pct?: number | null; impressions_delta_pct?: number | null };

const num = (value: unknown): number => (typeof value === "number" ? value : 0);
const str = (value: unknown): string => (value == null ? "" : String(value));

/** Python's `x or 0.0`: null/undefined/0/NaN all collapse to 0. */
const orZero = (value: unknown): number => {
  const n = typeof value === "number" ? value : 0;
  return n || 0;
};

const SORT_KEYS: Record<string, (row: ListRow) => number | string> = {
  score: (row) => num(row.score),
  title: (row) => str(row.title).toLowerCase(),
  updated_at: (row) => str(row.updated_at),
  content_status: (row) =>
    str(row.seo_title).trim().length > 0 &&
    str(row.seo_description).trim().length > 0 &&
    num(row.body_length) > 0
      ? 1
      : 0,
  gsc_segments: (row) =>
    (row.gsc_segment_flags as { has_dimensional?: boolean } | undefined)?.has_dimensional ? 1 : 0,
  index_status: (row) => str(row.index_status).toLowerCase(),
  gsc_impressions: (row) => num(row.gsc_impressions),
  gsc_clicks: (row) => num(row.gsc_clicks),
  gsc_ctr: (row) => num(row.gsc_ctr),
  // Python: `item["gsc_position"] or 999.0` -- 0 and null both become 999.
  gsc_position: (row) => (row.gsc_position ? num(row.gsc_position) : 999.0),
  ga4_sessions: (row) => num(row.ga4_sessions),
  ga4_views: (row) => num(row.ga4_views),
  body_length: (row) => num(row.body_length),
  pagespeed_performance: (row) =>
    row.pagespeed_performance != null ? num(row.pagespeed_performance) : -1,
  pagespeed_desktop_performance: (row) =>
    row.pagespeed_desktop_performance != null ? num(row.pagespeed_desktop_performance) : -1,
  gsc_clicks_delta: (row) => orZero((row.trend as Trendish | undefined)?.clicks_delta_pct),
  gsc_impressions_delta: (row) => orZero((row.trend as Trendish | undefined)?.impressions_delta_pct)
};

export const LIST_SORT_KEYS = new Set(Object.keys(SORT_KEYS));

export function sortListRows<T extends ListRow>(
  rows: readonly T[],
  sort: string,
  direction: "asc" | "desc"
): T[] {
  const keyOf = SORT_KEYS[sort] ?? SORT_KEYS.score;
  const reverse = direction !== "asc";
  // Decorate with the input index. Python's sorted(key=..., reverse=True) keeps
  // equal elements in input order; negating a JS comparator would flip them, so
  // ties fall back to the original position explicitly.
  const decorated = rows.map((row, index) => ({ row, index, key: keyOf(row) }));
  decorated.sort((a, b) => {
    let cmp: number;
    if (typeof a.key === "string" || typeof b.key === "string") {
      const as = String(a.key);
      const bs = String(b.key);
      cmp = as < bs ? -1 : as > bs ? 1 : 0;
    } else {
      cmp = a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
    }
    if (cmp !== 0) return reverse ? -cmp : cmp;
    return a.index - b.index;
  });
  return decorated.map((entry) => entry.row);
}

/** Sort key sent to the API. Ordering is applied client-side, so this is fixed
 *  to keep the request (and its cache entry) independent of the chosen column. */
export const CANONICAL_LIST_SORT = "score";
export const CANONICAL_LIST_DIRECTION = "desc";
