import { useQuery } from "@tanstack/react-query";
import { getJson } from "../lib/api";
import { storeInfoSchema } from "../types/api";

export function useStoreInfo() {
  return useQuery({
    queryKey: ["store-info"],
    queryFn: () => getJson("/api/store-info", storeInfoSchema),
    staleTime: 5 * 60 * 1000,
  });
}

export function useStoreUrl(): string {
  const { data } = useStoreInfo();
  return data?.store_url ?? "";
}
