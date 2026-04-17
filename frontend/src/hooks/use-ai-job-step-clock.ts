import { useEffect, useRef, useState } from "react";

/**
 * When an AI job step changes (step_index or stage), reset a client clock so the
 * toast can show accurate "this step" elapsed time. Server `stage_started_at` is
 * updated on every progress tick, so client-side boundaries are more reliable.
 */
export function useAiJobStepClock(isRunning: boolean, stepIndex: number, stage: string) {
  const [stepStartedAtMs, setStepStartedAtMs] = useState(() => Date.now());
  const lastKeyRef = useRef<string>("");

  useEffect(() => {
    if (!isRunning) {
      lastKeyRef.current = "";
      return;
    }
    const key = `${stepIndex}|${stage}`;
    if (key !== lastKeyRef.current) {
      lastKeyRef.current = key;
      setStepStartedAtMs(Date.now());
    }
  }, [isRunning, stepIndex, stage]);

  return stepStartedAtMs;
}
