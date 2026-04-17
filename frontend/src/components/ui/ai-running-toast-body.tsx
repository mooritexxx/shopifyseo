import type { ReactNode } from "react";

function formatDurationMs(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function AiRunningToastBody(props: {
  headline: string;
  stepElapsedMs: number;
}): ReactNode {
  const { headline, stepElapsedMs } = props;
  return (
    <div className="leading-snug text-left">
      <span>{headline}</span>
      <span className="text-slate-600"> — </span>
      <span className="font-medium tabular-nums">{formatDurationMs(stepElapsedMs)}</span>
    </div>
  );
}
