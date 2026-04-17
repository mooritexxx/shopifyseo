import { useQuery } from "@tanstack/react-query";
import { getJson } from "../lib/api";
import { statusSchema } from "../types/api";

const POLL_INTERVAL_MS = 1500;

/**
 * Poll /api/ai-status for a given job ID. Refetches every 1.5 s while the job
 * is running, stops when it finishes.
 */
export function useAiJobStatus(jobId: string) {
  return useQuery({
    queryKey: ["ai-status", jobId],
    queryFn: () =>
      getJson(
        `/api/ai-status?job_id=${encodeURIComponent(jobId)}`,
        statusSchema,
      ),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      query.state.data?.running ? POLL_INTERVAL_MS : false,
  });
}
