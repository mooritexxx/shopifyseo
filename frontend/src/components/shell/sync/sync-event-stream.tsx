import { useCallback, useLayoutEffect, useRef } from "react";
import type { SyncLogLine } from "./use-sync-event-log";

type Props = {
  lines: SyncLogLine[];
  accent: string;
};

/** Pixels from the bottom to still treat the user as "following" the tail (auto-scroll on new lines). */
const STICK_TO_BOTTOM_THRESHOLD_PX = 48;

export function SyncEventStream({ lines, accent }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  /** When true, new log lines keep the view pinned to the latest entry. */
  const stickToBottomRef = useRef(true);

  const updateStickFromScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distFromBottom <= STICK_TO_BOTTOM_THRESHOLD_PX;
  }, []);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines]);

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
        Event stream
        <span className="flex-1" />
        <span className="font-mono text-[10px] font-medium normal-case tracking-normal text-white/35">
          {lines.length} events
        </span>
      </div>
      <div
        ref={scrollRef}
        onScroll={updateStickFromScroll}
        className="sync-event-stream-mono max-h-[min(60vh,28rem)] overflow-y-auto px-3 py-1.5 text-[10.5px] leading-relaxed"
      >
        {lines.length === 0 ? (
          <div className="py-1.5 text-white/35">waiting for events…</div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className="flex gap-2.5 text-white/75">
              <span className="shrink-0 text-white/30">{l.t}</span>
              <span
                className="w-[70px] shrink-0 truncate pt-px text-[9.5px] font-semibold uppercase tracking-[0.05em]"
                style={{ color: accent }}
              >
                {l.tag}
              </span>
              <span className="min-w-0 flex-1 break-all whitespace-pre-wrap">{l.msg}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
