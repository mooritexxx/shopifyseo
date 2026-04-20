import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "../../../lib/utils";

const TAG_ERR = "#f87171";
const TAG_WARN = "#fbbf24";
const TAG_RUN = "#7dd3fc";
const TAG_OK = "#91efbb";
const TAG_MUTED = "rgba(255,255,255,0.42)";

const DEFAULT_HEADER_ACCENT = "oklch(0.62 0.18 262)";

const SEP = " — ";

export type SyncQueueDetailItem = {
  seq: number;
  object_type: string;
  handle: string;
  url: string;
  strategy?: string;
  /** Short status tag: HTTP n, RATE, 429, RUN, READY, WAIT, RETRY, ERR, … */
  code: string;
  state: string;
  /** Optional outcome hint (legacy / extensions). */
  outcome?: "downloaded" | "skip_unchanged" | "skip_304" | "error";
  error?: string;
  http_status?: number;
  response_body?: string;
};

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

function rowSummary(item: SyncQueueDetailItem): string {
  const err = (item.error || "").trim();
  if (err) return pagespeedErrorSummaryText(err);
  const st = (item.state || "").toLowerCase();
  if (st === "running") return "Running…";
  if (st === "deferred") return "Deferred for retry";
  if (st === "queued") return "Queued";
  return item.code || "—";
}

type Props = {
  title: string;
  items: SyncQueueDetailItem[];
  /** Dot + header accent (CSS color); defaults to pipeline purple. */
  headerAccent?: string;
};

export function SyncQueueTable({ title, items, headerAccent = DEFAULT_HEADER_ACCENT }: Props) {
  const uid = useId();
  const headerId = `${uid}-sync-queue-header`;
  const bodyId = `${uid}-sync-queue-body`;
  const scrollRef = useRef<HTMLDivElement>(null);
  const [openSeqs, setOpenSeqs] = useState<Set<number>>(new Set());
  const [collapsed, setCollapsed] = useState(true);

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
      <div className={cn("flex items-stretch", collapsed ? "" : "border-b border-white/[0.06]")}>
        <button
          type="button"
          id={headerId}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45 transition-colors",
            "hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-white/25"
          )}
          aria-expanded={!collapsed}
          aria-controls={collapsed ? undefined : bodyId}
          onClick={() => setCollapsed((c) => !c)}
        >
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{
              background: headerAccent,
              animation: "syncDrawerBlink 0.9s ease-in-out infinite"
            }}
          />
          {title}
          <span className="flex-1" />
        </button>
        <div className="flex shrink-0 items-center gap-1.5 pr-3">
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
            {items.length} {items.length === 1 ? "event" : "events"}
          </span>
        </div>
      </div>
      {!collapsed ? (
        <div id={bodyId} role="region" aria-labelledby={headerId}>
          <p className="border-b border-white/[0.06] px-3 py-1.5 text-[9.5px] leading-snug text-white/40">
            Live stream: status code, object, and detail on expand. Successful events leave the list as they finish.
          </p>
          <div
            ref={scrollRef}
            className="sync-event-stream-mono max-h-[min(42vh,320px)] overflow-y-auto px-0 py-0 text-[10.5px] leading-relaxed"
          >
            {items.length === 0 ? (
              <div className="px-3 py-2 text-white/35">No queue events.</div>
            ) : (
              items.map((item, index) => {
                const seq = item.seq ?? index;
                const isOpen = openSeqs.has(seq);
                const summary = rowSummary(item);
                const tagColor = tagStyle(item.code, item.http_status);
                const fivexx = item.http_status != null && item.http_status >= 500 && item.http_status <= 504;

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
      ) : null}
    </div>
  );
}
