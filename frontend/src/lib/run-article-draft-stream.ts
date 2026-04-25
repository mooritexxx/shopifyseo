import { articleGenerateDraftResultSchema, type ArticleGenerateDraftResult } from "../types/api";

export type ArticleDraftProgressEvent = {
  message: string;
  phase?: string;
  state?: string;
  run_id?: string;
  step_key?: string;
  step_label?: string;
  step_index?: number;
  step_total?: number;
  item_done?: number;
  item_total?: number;
  result_summary?: string;
  /** Planned image jobs: 1 featured cover + section images */
  images_total?: number;
  /** Successful uploads so far (featured + section images completed) */
  images_done?: number;
};

export type ArticleDraftStreamPayload = {
  blog_id: string;
  blog_handle: string;
  topic: string;
  keywords: string[];
  author_name: string;
  /** If non-empty, used as the source for the Shopify handle (slugified). If empty, handle comes from the AI headline. */
  slug_hint: string;
  /** If set, the generated article will be linked back to this idea. */
  idea_id?: number;
  /** Optional angle label when generating multiple articles from one idea. */
  angle_label?: string;
  /** If set, regenerate an existing article in place (same URL) instead of creating a new draft. */
  regenerate_article_handle?: string;
  /** If set, resume a persisted draft run from its last checkpoint. */
  resume_run_id?: string;
};

/**
 * POST JSON body to `/api/articles/generate-draft-stream` and parse SSE (progress / done / error).
 */
export async function runArticleDraftStream(
  body: ArticleDraftStreamPayload,
  onProgress: (evt: ArticleDraftProgressEvent) => void
): Promise<ArticleGenerateDraftResult> {
  const res = await fetch("/api/articles/generate-draft-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store"
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text.trim() || res.statusText || "Request failed");
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("No response body");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload: unknown = null;
  let errorDetail: string | null = null;

  let eof = false;
  while (!eof) {
    const { done, value } = await reader.read();
    if (done) {
      eof = true;
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    let eventType = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const raw = line.slice(6);
        const data = JSON.parse(raw) as Record<string, unknown>;
        if (eventType === "progress" && typeof data.message === "string") {
          onProgress({
            message: data.message,
            phase: typeof data.phase === "string" ? data.phase : undefined,
            state: typeof data.state === "string" ? data.state : undefined,
            run_id: typeof data.run_id === "string" ? data.run_id : undefined,
            step_key: typeof data.step_key === "string" ? data.step_key : undefined,
            step_label: typeof data.step_label === "string" ? data.step_label : undefined,
            step_index: typeof data.step_index === "number" ? data.step_index : undefined,
            step_total: typeof data.step_total === "number" ? data.step_total : undefined,
            item_done: typeof data.item_done === "number" ? data.item_done : undefined,
            item_total: typeof data.item_total === "number" ? data.item_total : undefined,
            result_summary: typeof data.result_summary === "string" ? data.result_summary : undefined,
            images_total: typeof data.images_total === "number" ? data.images_total : undefined,
            images_done: typeof data.images_done === "number" ? data.images_done : undefined
          });
        } else if (eventType === "done") {
          donePayload = data;
        } else if (eventType === "error") {
          errorDetail = typeof data.detail === "string" ? data.detail : "Generation failed";
        }
      }
    }
  }

  if (errorDetail) {
    throw new Error(errorDetail);
  }
  if (!donePayload) {
    throw new Error("Stream ended without a result");
  }

  return articleGenerateDraftResultSchema.parse(donePayload);
}
