/**
 * Google Ads keyword list import: single column header "Keyword" (see template).
 * RFC 4180-style escaping for commas, quotes, and newlines inside a keyword.
 */
export const GOOGLE_ADS_KEYWORDS_CSV_HEADER = "Keyword";

function csvEscapeCell(value: string): string {
  if (/[",\r\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/** Build CSV bytes matching the template: header `Keyword`, then one keyword per row. */
export function buildGoogleAdsKeywordsCsv(keywords: string[]): string {
  const sorted = [...new Set(keywords.map((k) => k.trim()).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: "base" }),
  );
  const lines = [GOOGLE_ADS_KEYWORDS_CSV_HEADER, ...sorted.map(csvEscapeCell)];
  return `${lines.join("\n")}\n`;
}

export function downloadGoogleAdsKeywordsCsv(keywords: string[], filename = "keywords-template.csv") {
  const csv = buildGoogleAdsKeywordsCsv(keywords);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
