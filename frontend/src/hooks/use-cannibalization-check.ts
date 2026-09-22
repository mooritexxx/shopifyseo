import { useQuery } from "@tanstack/react-query";
import { getJson } from "../lib/api";
import { cannibalizationCheckPayloadSchema, type CannibalizationCheckPayload } from "../types/api";

export function useCannibalizationCheck(ideaId: number, blogHandle?: string) {
  const params = new URLSearchParams();
  if (blogHandle) {
    params.set("blog_handle", blogHandle);
  }
  const queryString = params.toString();
  const path = `/api/article-ideas/${ideaId}/cannibalization-check${queryString ? `?${queryString}` : ""}`;

  return useQuery({
    queryKey: ["cannibalization-check", ideaId, blogHandle ?? null],
    queryFn: () => getJson(path, cannibalizationCheckPayloadSchema),
    enabled: ideaId > 0,
    staleTime: 30 * 1000,
  });
}

export type { CannibalizationCheckPayload };
