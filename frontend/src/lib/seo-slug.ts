/**
 * Mirror of `shopifyseo.seo_slug.slugify_article_handle` for preview / default URL slug in the UI.
 */
export function slugifyArticleHandle(text: string, maxLen = 96): string {
  const raw = (text || "").trim();
  if (!raw) {
    return "article";
  }

  const normalized = raw.normalize("NFKD");
  let ascii = "";
  for (let i = 0; i < normalized.length; i++) {
    const code = normalized.charCodeAt(i);
    if (code <= 0x7f) {
      ascii += normalized[i];
    }
  }

  let slug = ascii.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  slug = slug.replace(/-+/g, "-").replace(/^-|-$/g, "");

  if (!slug) {
    return "article";
  }

  if (slug.length > maxLen) {
    slug = slug.slice(0, maxLen).replace(/-+$/, "").replace(/-+/g, "-").replace(/^-|-$/g, "");
  }

  return slug || "article";
}

/** Words that add no SEO value in a URL slug. */
const STOP_WORDS = new Set([
  "a","an","the","and","or","but","in","on","at","to","for","of","with",
  "is","are","was","were","be","been","being","have","has","had",
  "do","does","did","will","would","shall","should","may","might",
  "can","could","about","from","into","through","during","before",
  "after","above","below","between","under","over","out","up","down",
  "off","then","than","so","no","not","only","very","just","how",
  "what","when","where","which","who","whom","why","all","each",
  "every","both","few","more","most","other","some","such","own",
  "same","too","also","your","you","its","our","their","my","this",
  "that","these","those","here","there","again","once","i","we","he",
  "she","it","they","me","him","her","us","them","need","know",
  "everything","nothing","something","anything","guide","complete",
  "ultimate","best","top","rated",
]);

/**
 * Build a concise, keyword-rich slug source from topic + keywords.
 *
 * Strategy:
 * 1. Extract meaningful words from the topic (strip stop words + filler).
 * 2. Prepend any keywords that aren't already present in the topic words.
 * 3. Target 3-5 words total — enough for search intent, short enough for clean URLs.
 *
 * Example:
 *   topic = "Everything You Need to Know About SMOK Novo Pod Systems"
 *   keywords = "novo, pod, coils"
 *   → "smok-novo-pod-systems"
 */
export function buildDraftSlugSource(topic: string, keywordsCsv: string): string {
  const kw = keywordsCsv
    .split(",")
    .map((k) => k.trim().toLowerCase())
    .filter(Boolean);

  // Extract meaningful words from the topic
  const topicWords = topic
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length >= 2 && !STOP_WORDS.has(w));

  // Start with topic words (up to 5), then add unique keyword terms
  const seen = new Set<string>();
  const parts: string[] = [];

  for (const w of topicWords) {
    if (!seen.has(w) && parts.length < 5) {
      seen.add(w);
      parts.push(w);
    }
  }

  // Add keyword terms not already covered by the topic
  for (const phrase of kw) {
    for (const w of phrase.split(/\s+/)) {
      const clean = w.replace(/[^a-z0-9]/g, "");
      if (clean.length >= 2 && !seen.has(clean) && parts.length < 5) {
        seen.add(clean);
        parts.push(clean);
      }
    }
  }

  return parts.length > 0 ? parts.join(" ") : topic.trim();
}

export function defaultDraftSlugHint(topic: string, keywordsCsv: string): string {
  return slugifyArticleHandle(buildDraftSlugSource(topic, keywordsCsv));
}
