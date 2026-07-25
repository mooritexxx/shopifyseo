import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";
import { Toaster } from "../components/ui/sonner";

// Without a default staleTime every query refetches on each mount, so simply
// navigating away from a list and back re-ran the full request. Mutations still
// refresh immediately: the ~59 invalidateQueries call sites mark their queries
// stale regardless of this value. Detail pages that must always read through
// set staleTime: 0 themselves.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000
    }
  }
});

export function Providers({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster position="bottom-right" />
    </QueryClientProvider>
  );
}
