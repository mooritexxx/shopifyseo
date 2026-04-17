import { useEffect, useRef, useState } from "react";

export type SyncLogLine = { t: string; tag: string; msg: string };

const MAX = 48;

function formatLogTime(d: Date) {
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  const ds = String(Math.floor(d.getMilliseconds() / 100));
  return `${m}:${s}.${ds}`;
}

/** Append when `current` changes (poll ticks) to approximate the prototype event stream. */
export function useSyncEventLog(current: string | undefined, activeScope: string, running: boolean) {
  const [lines, setLines] = useState<SyncLogLine[]>([]);
  const prevRef = useRef<string | null>(null);

  useEffect(() => {
    if (!running) return;
    const msg = (current || "").trim();
    if (!msg) return;
    if (prevRef.current === msg) return;
    prevRef.current = msg;
    const tag = (activeScope || "sync").slice(0, 12);
    setLines((prev) => {
      const next = [...prev, { t: formatLogTime(new Date()), tag, msg }];
      return next.length > MAX ? next.slice(-MAX) : next;
    });
  }, [current, activeScope, running]);

  useEffect(() => {
    if (!running) {
      prevRef.current = null;
    }
  }, [running]);

  /** Push a synthetic line (e.g. error summary) without requiring `running`. */
  const pushLine = (tag: string, msg: string) => {
    setLines((prev) => {
      const next = [...prev, { t: formatLogTime(new Date()), tag, msg }];
      return next.length > MAX ? next.slice(-MAX) : next;
    });
  };

  const clear = () => setLines([]);

  return { lines, pushLine, clear };
}
