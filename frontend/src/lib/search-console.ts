export function isSearchConsoleInspectionLink(href?: string | null): boolean {
  const raw = (href || "").trim();
  if (!raw) return false;
  try {
    const url = new URL(raw);
    return url.hostname === "search.google.com" && url.pathname.includes("/search-console/inspect");
  } catch {
    return raw.includes("search.google.com/search-console/inspect");
  }
}
