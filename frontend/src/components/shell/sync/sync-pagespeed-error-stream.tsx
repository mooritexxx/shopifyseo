import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, ClipboardCopy } from "lucide-react";
import { cn } from "../../../lib/utils";

/** Keep aligned with `PAGESPEED_ERROR_DETAILS_MAX` in `shopifyseo/dashboard_actions/_state.py`. */
const PAGESPEED_ERROR_LOG_CAP = 500;

/** Header pulse + HTTP / ERR tags — red so errors read clearly vs pipeline accent. */
const ERROR_STREAM_RED = "#f87171";

export type PagespeedErrorDetailItem = {
  object_type: string;
  handle: string;
  url: string;
  strategy?: string;
  error: string;
  http_status?: number;
  response_body?: string;
  /** Stable row id from server (falls back to index if missing). */
  seq?: number;
};

const SEP = " — ";

/** Strip the long `HTTP n for https://...` prefix for a compact summary line. */
function pagespeedErrorSummaryText(error: string): string {
  const sep = error.indexOf(SEP);
  if (sep >= 0) {
    return error.slice(sep + SEP.length).trim() || error;
  }
  return error.replace(/^HTTP \d+ for https?:\/\/\S+/i, "").trim() || error;
}

function formatErrorsForClipboard(items: PagespeedErrorDetailItem[]): string {
  if (items.length === 0) {
    return "No errors this run.";
  }
  return items
    .map((item, i) => {
      const parts = [
        `--- ${i + 1} ---`,
        `object: ${item.object_type}:${item.handle}`,
        item.strategy ? `strategy: ${item.strategy}` : null,
        `url: ${item.url}`,
        item.seq != null ? `seq: ${item.seq}` : null,
        item.http_status != null ? `http_status: ${item.http_status}` : null,
        `error: ${item.error}`,
        item.response_body ? `response_body:\n${item.response_body}` : null
      ].filter((p): p is string => p != null && p !== "");
      return parts.join("\n");
    })
    .join("\n\n");
}

type Props = {
  items: PagespeedErrorDetailItem[];
};

export function PageSpeedErrorStream({ items }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [openSeqs, setOpenSeqs] = useState<Set<number>>(new Set());
  const [collapsed, setCollapsed] = useState(false);
  const [copyDone, setCopyDone] = useState(false);
  const copyResetRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);

  const copyAllErrors = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(formatErrorsForClipboard(items));
      if (copyResetRef.current != null) window.clearTimeout(copyResetRef.current);
      setCopyDone(true);
      copyResetRef.current = window.setTimeout(() => {
        setCopyDone(false);
        copyResetRef.current = null;
      }, 2000);
    } catch {
      /* clipboard may be denied */
    }
  }, [items]);

  useEffect(
    () => () => {
      if (copyResetRef.current != null) window.clearTimeout(copyResetRef.current);
    },
    []
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  const toggle = (seq: number) => {
    setOpenSeqs((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });
  };

  return (
    <div className="overflow-hidden rounded-xl border border-white/[0.06] bg-black/30">
      <div
        className={cn(
          "flex items-stretch",
          collapsed ? "" : "border-b border-white/[0.06]"
        )}
      >
        <button
          type="button"
          id="pagespeed-error-stream-header"
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45 transition-colors",
            "hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
          )}
          aria-expanded={!collapsed}
          aria-controls="pagespeed-error-stream-body"
          onClick={() => setCollapsed((c) => !c)}
        >
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{
              background: ERROR_STREAM_RED,
              animation: "syncDrawerBlink 0.9s ease-in-out infinite"
            }}
          />
          Error Stream
          <span className="flex-1" />
          <span className="font-mono text-[10px] font-medium normal-case tracking-normal text-white/35">
            {items.length} {items.length === 1 ? "row" : "rows"}
          </span>
        </button>
        <button
          type="button"
          className={cn(
            "flex shrink-0 items-center justify-center px-1.5 py-2 text-white/35 transition-colors",
            "hover:bg-white/[0.06] hover:text-white/55",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
          )}
          aria-label={copyDone ? "Copied to clipboard" : "Copy all errors to clipboard"}
          onClick={() => void copyAllErrors()}
        >
          {copyDone ? (
            <Check className="h-3.5 w-3.5 text-[#91efbb]" strokeWidth={2.5} aria-hidden />
          ) : (
            <ClipboardCopy className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
          )}
        </button>
        <button
          type="button"
          className={cn(
            "flex shrink-0 items-center pr-3 pl-0.5 py-2 text-white/35 transition-colors",
            "hover:bg-white/[0.04] hover:text-white/50",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
          )}
          aria-label={collapsed ? "Expand error stream" : "Collapse error stream"}
          onClick={() => setCollapsed((c) => !c)}
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
          )}
        </button>
      </div>
      <div
        id="pagespeed-error-stream-body"
        role="region"
        aria-labelledby="pagespeed-error-stream-header"
        hidden={collapsed}
      >
        <p className="border-b border-white/[0.06] px-3 py-1.5 text-[9.5px] leading-snug text-white/40">
          Click a row for storefront URL, full message, and raw JSON. Up to {PAGESPEED_ERROR_LOG_CAP} rows per sync;
          older rows roll off after that.
        </p>
        <div
          ref={scrollRef}
          className="sync-event-stream-mono max-h-[min(42vh,320px)] overflow-y-auto px-0 py-0 text-[10.5px] leading-relaxed"
        >
        {items.length === 0 ? (
          <div className="px-3 py-2 text-white/35">No errors this run.</div>
        ) : (
          items.map((item, index) => {
            const seq = item.seq ?? index;
            const isOpen = openSeqs.has(seq);
            const summary = pagespeedErrorSummaryText(item.error);
            const status = item.http_status;
            const tag =
              status != null ? `HTTP ${status}` : item.error.startsWith("HTTP") ? "HTTP ?" : "ERR";
            const fivexx = status != null && status >= 500 && status <= 504;

            return (
              <div key={seq} className="border-b border-white/[0.05] last:border-b-0">
                <button
                  type="button"
                  onClick={() => toggle(seq)}
                  className={cn(
                    "flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors hover:bg-white/[0.04]",
                    isOpen && "bg-white/[0.03]"
                  )}
                >
                  <ChevronDown
                    size={14}
                    className={cn("mt-0.5 shrink-0 text-white/35 transition-transform", isOpen && "rotate-180")}
                    aria-hidden
                  />
                  <span
                    className="w-[72px] shrink-0 pt-px text-[9.5px] font-semibold uppercase tracking-[0.05em]"
                    style={{ color: ERROR_STREAM_RED }}
                  >
                    {tag}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="text-white/55">
                      {item.object_type}:{item.handle}
                      {item.strategy ? <span className="text-white/35"> · {item.strategy}</span> : null}
                    </span>
                    <span className="mt-0.5 block truncate text-white/70" title={summary}>
                      {summary}
                    </span>
                  </span>
                </button>
                {isOpen ? (
                  <div className="space-y-2 border-t border-white/[0.06] bg-black/35 px-3 py-2.5 text-[10px] text-white/60">
                    <div>
                      <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-white/35">
                        Storefront URL
                      </div>
                      <div className="break-all text-white/75">{item.url}</div>
                    </div>
                    <div>
                      <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-white/35">
                        Full message
                      </div>
                      <div className="break-all text-white/70">{item.error}</div>
                    </div>
                    {fivexx ? (
                      <p className="text-[9.5px] leading-relaxed text-white/45">
                        Google often returns a generic line for HTTP 5xx; there is rarely more detail in the payload.
                        Retry later, lower concurrency, or test the URL on pagespeed.web.dev.
                      </p>
                    ) : null}
                    {item.response_body ? (
                      <div>
                        <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-white/35">
                          Raw API response
                        </div>
                        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-black/40 p-2 text-[9.5px] leading-snug text-white/55">
                          {item.response_body}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
        </div>
      </div>
    </div>
  );
}
