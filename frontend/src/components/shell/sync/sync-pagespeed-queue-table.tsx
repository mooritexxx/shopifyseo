import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, ClipboardCopy } from "lucide-react";
import { cn } from "../../../lib/utils";

/** Keep aligned with `PAGESPEED_ERROR_DETAILS_MAX` in `shopifyseo/dashboard_actions/_state.py`. */
const PAGESPEED_QUEUE_LOG_CAP = 500;

const TAG_ERR = "#f87171";
const TAG_WARN = "#fbbf24";
const TAG_RUN = "#7dd3fc";
const TAG_OK = "#91efbb";
const TAG_MUTED = "rgba(255,255,255,0.42)";

/** Pipeline accent — queue is live work, not only failures. */
const QUEUE_HEADER_ACCENT = "oklch(0.62 0.18 262)";

export type PagespeedQueueDetailItem = {
  seq: number;
  object_type: string;
  handle: string;
  url: string;
  strategy?: string;
  /** Short status tag: HTTP n, RATE, 429, RUN, READY, WAIT, RETRY, ERR, … */
  code: string;
  state: string;
  error?: string;
  http_status?: number;
  response_body?: string;
};

const SEP = " — ";

function pagespeedErrorSummaryText(error: string): string {
  const sep = error.indexOf(SEP);
  if (sep >= 0) {
    return error.slice(sep + SEP.length).trim() || error;
  }
  return error.replace(/^HTTP \d+ for https?:\/\/\S+/i, "").trim() || error;
}

function tagStyle(code: string, httpStatus?: number): string {
  const c = code.toUpperCase();
  if (c === "ERR" || c.startsWith("HTTP 5")) return TAG_ERR;
  if (httpStatus != null && httpStatus >= 400 && httpStatus < 500) return TAG_WARN;
  if (c === "RATE" || c === "429" || c === "RETRY") return TAG_WARN;
  if (c === "RUN") return TAG_RUN;
  if (c === "READY") return TAG_OK;
  return TAG_MUTED;
}

function rowSummary(item: PagespeedQueueDetailItem): string {
  const err = (item.error || "").trim();
  if (err) return pagespeedErrorSummaryText(err);
  const st = (item.state || "").toLowerCase();
  if (st === "running") return "Running PageSpeed…";
  if (st === "deferred") return "Deferred for retry";
  if (st === "queued") return "Queued";
  return item.code || "—";
}

function formatQueueForClipboard(items: PagespeedQueueDetailItem[]): string {
  if (items.length === 0) {
    return "No queue rows.";
  }
  return items
    .map((item, i) => {
      const parts = [
        `--- ${i + 1} ---`,
        `code: ${item.code}`,
        `state: ${item.state}`,
        `object: ${item.object_type}:${item.handle}`,
        item.strategy ? `strategy: ${item.strategy}` : null,
        `url: ${item.url}`,
        `seq: ${item.seq}`,
        item.http_status != null ? `http_status: ${item.http_status}` : null,
        item.error ? `error: ${item.error}` : null,
        item.response_body ? `response_body:\n${item.response_body}` : null
      ].filter((p): p is string => p != null && p !== "");
      return parts.join("\n");
    })
    .join("\n\n");
}

type Props = {
  items: PagespeedQueueDetailItem[];
};

export function PageSpeedQueueTable({ items }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [openSeqs, setOpenSeqs] = useState<Set<number>>(new Set());
  const [collapsed, setCollapsed] = useState(false);
  const [copyDone, setCopyDone] = useState(false);
  const copyResetRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);

  const copyAll = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(formatQueueForClipboard(items));
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
          id="pagespeed-queue-table-header"
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45 transition-colors",
            "hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
          )}
          aria-expanded={!collapsed}
          aria-controls="pagespeed-queue-table-body"
          onClick={() => setCollapsed((c) => !c)}
        >
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{
              background: QUEUE_HEADER_ACCENT,
              animation: "syncDrawerBlink 0.9s ease-in-out infinite"
            }}
          />
          PageSpeed queue
          <span className="flex-1" />
        </button>
        <div className="flex shrink-0 items-center gap-1.5 pr-3">
          <button
            type="button"
            className={cn(
              "flex items-center justify-center rounded p-1 text-white/35 transition-colors",
              "hover:bg-white/[0.06] hover:text-white/55",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
            )}
            aria-label={copyDone ? "Copied to clipboard" : "Copy queue snapshot to clipboard"}
            onClick={() => void copyAll()}
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
              "flex items-center text-white/35 transition-colors",
              "hover:text-white/50",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
            )}
            aria-label={collapsed ? "Expand queue table" : "Collapse queue table"}
            onClick={() => setCollapsed((c) => !c)}
          >
            {collapsed ? (
              <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
            )}
          </button>
          <span className="ml-1 font-mono text-[10px] font-medium normal-case tracking-normal text-white/35">
            {items.length} {items.length === 1 ? "row" : "rows"}
          </span>
        </div>
      </div>
      <div
        id="pagespeed-queue-table-body"
        role="region"
        aria-labelledby="pagespeed-queue-table-header"
        hidden={collapsed}
      >
        <p className="border-b border-white/[0.06] px-3 py-1.5 text-[9.5px] leading-snug text-white/40">
          Live rows: status code, object, and detail on expand. Up to {PAGESPEED_QUEUE_LOG_CAP} rows; older rows roll off.
        </p>
        <div
          ref={scrollRef}
          className="sync-event-stream-mono max-h-[min(42vh,320px)] overflow-y-auto px-0 py-0 text-[10.5px] leading-relaxed"
        >
          {items.length === 0 ? (
            <div className="px-3 py-2 text-white/35">No queue rows.</div>
          ) : (
            items.map((item, index) => {
              const seq = item.seq ?? index;
              const isOpen = openSeqs.has(seq);
              const summary = rowSummary(item);
              const tagColor = tagStyle(item.code, item.http_status);
              const fivexx =
                item.http_status != null && item.http_status >= 500 && item.http_status <= 504;

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
                      style={{ color: tagColor }}
                    >
                      {item.code}
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
                          State
                        </div>
                        <div className="break-all text-white/70">{item.state}</div>
                      </div>
                      {item.error ? (
                        <div>
                          <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-white/35">
                            Message
                          </div>
                          <div className="break-all text-white/70">{item.error}</div>
                        </div>
                      ) : null}
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
