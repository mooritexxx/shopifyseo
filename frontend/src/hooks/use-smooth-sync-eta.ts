import { useEffect, useRef, useState } from "react";

/** Wall seconds per displayed countdown second when server ETA is flat (calm UI). */
const DISPLAY_STRETCH = 1.07;
/** Max display-seconds added per real second when server revises ETA upward. */
const UPWARD_MAX_PER_SEC = 1.2;
const TICK_MS = 250;

/**
 * Smooths sync `eta_seconds` for display: stretched decay when flat, fast catch-up
 * when the server drops the estimate, capped upward slew when it rises.
 */
export function useSmoothSyncEta(
  running: boolean,
  physicsEta: number | null | undefined,
  stage: string | undefined,
  activeScope: string | undefined
): number | null {
  const [displayInt, setDisplayInt] = useState<number | null>(null);
  const displayRemain = useRef<number | null>(null);
  const prevPhysics = useRef<number | null>(null);
  const lastSegmentKey = useRef("");
  const physicsRef = useRef<number | null>(null);

  useEffect(() => {
    physicsRef.current =
      physicsEta != null && physicsEta >= 0 && Number.isFinite(physicsEta)
        ? Math.floor(physicsEta)
        : null;
  }, [physicsEta]);

  useEffect(() => {
    if (!running) {
      displayRemain.current = null;
      prevPhysics.current = null;
      lastSegmentKey.current = "";
      setDisplayInt(null);
      return;
    }

    const id = window.setInterval(() => {
      const physics = physicsRef.current;
      const segmentKey = `${stage ?? ""}|${activeScope ?? ""}`;
      const dt = TICK_MS / 1000;

      if (physics == null) {
        displayRemain.current = null;
        prevPhysics.current = null;
        setDisplayInt(null);
        return;
      }

      if (segmentKey !== lastSegmentKey.current) {
        lastSegmentKey.current = segmentKey;
        displayRemain.current = physics;
        prevPhysics.current = physics;
        setDisplayInt(Math.max(0, Math.ceil(physics)));
        return;
      }

      let d = displayRemain.current ?? physics;
      const prev = prevPhysics.current ?? physics;

      if (physics < prev) {
        d = Math.max(physics, d - (prev - physics));
      } else if (physics > prev) {
        d = Math.min(physics, d + UPWARD_MAX_PER_SEC * dt);
      } else {
        d = Math.max(physics, d - dt / DISPLAY_STRETCH);
      }

      displayRemain.current = d;
      prevPhysics.current = physics;
      setDisplayInt(Math.max(0, Math.ceil(d)));
    }, TICK_MS);

    return () => clearInterval(id);
  }, [running, stage, activeScope]);

  return displayInt;
}
