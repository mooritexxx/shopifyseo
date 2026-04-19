import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type SyncLogLine = { t: string; tag: string; msg: string };

export type SyncServerEvent = { at: number; tag: string; msg: string };

function formatLogTime(d: Date) {
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  const ds = String(Math.floor(d.getMilliseconds() / 100));
  return `${h}:${m}:${s}.${ds}`;
}

function formatServerEventAt(atSeconds: number) {
  const d = new Date(atSeconds * 1000);
  return formatLogTime(d);
}

/**
 * Builds the drawer event stream from server ``sync_events`` (full history, no cap) plus optional
 * client-only lines (e.g. error summary) from ``pushLine``.
 */
export function useSyncEventLog(serverEvents: SyncServerEvent[] | undefined, running: boolean) {
  const [clientLines, setClientLines] = useState<SyncLogLine[]>([]);
  const prevRunning = useRef(false);

  useEffect(() => {
    if (running && !prevRunning.current) {
      setClientLines([]);
    }
    prevRunning.current = running;
  }, [running]);

  const serverLines = useMemo(() => {
    const rows = serverEvents ?? [];
    return rows.map((e) => ({
      t: formatServerEventAt(e.at),
      tag: (e.tag || "sync").slice(0, 12),
      msg: e.msg
    }));
  }, [serverEvents]);

  const lines = useMemo(() => [...serverLines, ...clientLines], [serverLines, clientLines]);

  const pushLine = useCallback((tag: string, msg: string) => {
    setClientLines((prev) => [...prev, { t: formatLogTime(new Date()), tag, msg }]);
  }, []);

  const clear = useCallback(() => {
    setClientLines([]);
  }, []);

  return { lines, pushLine, clear };
}
