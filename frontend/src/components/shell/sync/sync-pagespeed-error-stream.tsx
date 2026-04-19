import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../../../lib/utils";

/** Keep aligned with `PAGESPEED_ERROR_DETAILS_MAX` in `shopifyseo/dashboard_actions/_state.py`. */
const PAGESPEED_ERROR_LOG_CAP = 500;

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

type Props = {
  items: PagespeedErrorDetailItem[];
  accent: string;
};

export function PageSpeedErrorStream({ items, accent }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [openSeqs, setOpenSeqs] = useState<Set<number>>(new Set());

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
      <div className="flex items-center gap-2 border-b border-white/[0.06] px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45">
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: accent,
            animation: "syncDrawerBlink 0.9s ease-in-out infinite"
          }}
        />
        PageSpeed errors
        <span className="flex-1" />
        <span className="font-mono text-[10px] font-medium normal-case tracking-normal text-white/35">
          {items.length} {items.length === 1 ? "row" : "rows"}
        </span>
      </div>
      <p className="border-b border-white/[0.06] px-3 py-1.5 text-[9.5px] leading-snug text-white/40">
        Click a row for storefront URL, full message, and raw JSON. Up to {PAGESPEED_ERROR_LOG_CAP} rows per sync;
        older rows roll off after that.
      </p>
      <div
        ref={scrollRef}
        className="sync-event-stream-mono max-h-[min(42vh,320px)] overflow-y-auto px-0 py-0 text-[10.5px] leading-relaxed"
      >
        {items.length === 0 ? (
          <div className="px-3 py-2 text-white/35">No PageSpeed errors this run.</div>
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
                    style={{ color: accent }}
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
  );
}
