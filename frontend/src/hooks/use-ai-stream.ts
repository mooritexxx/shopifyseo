import { useEffect, useRef, useState } from "react";

export interface AiStreamField {
  field: string;
  value: string;
}

export interface AiStreamState {
  connected: boolean;
  fields: AiStreamField[];
  stage: string;
  stageLabel: string;
  stepIndex: number;
  stepTotal: number;
  activeModel: string;
  done: boolean;
  error: string;
}

const INITIAL_STATE: AiStreamState = {
  connected: false,
  fields: [],
  stage: "",
  stageLabel: "",
  stepIndex: 0,
  stepTotal: 0,
  activeModel: "",
  done: false,
  error: "",
};

/**
 * Opens an SSE connection to /api/ai-stream for the given job ID.
 * Returns the accumulated stream state with field values as they arrive.
 * The connection auto-closes when the job finishes or the component unmounts.
 */
export function useAiStream(jobId: string | null): AiStreamState {
  const [state, setState] = useState<AiStreamState>(INITIAL_STATE);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!jobId) {
      setState(INITIAL_STATE);
      return;
    }

    const es = new EventSource(`/api/ai-stream?job_id=${encodeURIComponent(jobId)}`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setState((prev) => {
          switch (data.type) {
            case "connected":
              return { ...prev, connected: true };

            case "progress":
              return {
                ...prev,
                stage: data.stage ?? prev.stage,
                stageLabel: data.stage_label ?? prev.stageLabel,
                stepIndex: data.step_index ?? prev.stepIndex,
                stepTotal: data.step_total ?? prev.stepTotal,
                activeModel: data.active_model ?? prev.activeModel,
              };

            case "field_complete":
              return {
                ...prev,
                fields: [...prev.fields, { field: data.field, value: data.value }],
              };

            case "done":
              return { ...prev, done: true, stage: data.stage ?? "complete", stageLabel: data.stage_label ?? "" };

            case "error":
              return { ...prev, done: true, error: data.message ?? "Generation failed" };

            case "cancelled":
              return { ...prev, done: true, stage: "cancelled", stageLabel: data.stage_label ?? "Cancelled" };

            default:
              return prev;
          }
        });

        if (data.type === "done" || data.type === "error" || data.type === "cancelled") {
          es.close();
        }
      } catch {
        // Ignore malformed events
      }
    };

    es.onerror = () => {
      setState((prev) => (prev.done ? prev : { ...prev, connected: false }));
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [jobId]);

  return state;
}
