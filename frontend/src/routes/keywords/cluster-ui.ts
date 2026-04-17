import { CONTENT_TYPE_LABELS } from "./badges";

/** Maps cluster `content_type` (from clustering LLM) to expected Shopify `match_type` values. */
const PLANNED_TO_MATCH_TYPES: Record<string, string[]> = {
  blog_post: ["blog_article"],
  buying_guide: ["blog_article"],
  collection_page: ["collection"],
  product_page: ["product"],
  landing_page: ["page"],
};

const MATCH_TYPE_LABELS: Record<string, string> = {
  collection: "Collection",
  page: "Page",
  blog_article: "Blog article",
  product: "Product",
};

/**
 * When the suggested Shopify URL type does not match the cluster's planned format,
 * return a short explainer for the UI (null = no mismatch or N/A).
 */
export function clusterFormatMatchHint(
  contentType: string | null | undefined,
  matchType: string | null | undefined,
): string | null {
  if (!matchType || matchType === "new" || matchType === "none") return null;
  const expected = PLANNED_TO_MATCH_TYPES[contentType ?? ""] ?? [];
  if (!expected.length) return null;
  if (expected.includes(matchType)) return null;
  const linked = MATCH_TYPE_LABELS[matchType] ?? matchType;
  const planned =
    (contentType && CONTENT_TYPE_LABELS[contentType]) || contentType?.replace(/_/g, " ") || "—";
  return `Planned format (${planned}) differs from the linked Shopify type (${linked}). The link is chosen for topical fit, not page kind.`;
}

export function suggestedMatchHref(matchType: string, matchHandle: string): string {
  if (matchType === "collection") return `/collections/${matchHandle}`;
  if (matchType === "page") return `/pages/${matchHandle}`;
  if (matchType === "blog_article") {
    const [blogHandle, articleHandle] = matchHandle.split("/", 2);
    if (blogHandle && articleHandle) return `/articles/${blogHandle}/${articleHandle}`;
  }
  return "#";
}
