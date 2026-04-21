export type KeywordResearchSseOptions = {
  /** JSON body for POST (e.g. `{ keywords: string[] }`). Omit for empty-body streams. */
  body?: unknown;
};

/** POST an SSE research endpoint; invokes callbacks for progress / terminal states. */
export function startKeywordResearchSse(
  url: string,
  callbacks: {
    onProgress: (message: string) => void;
    onDone: () => void;
    onError: (detail: string) => void;
  },
  options?: KeywordResearchSseOptions
): void {
  const { onProgress, onDone, onError } = callbacks;
  onProgress("Starting…");

  const init: RequestInit = { method: "POST" };
  if (options?.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }

  fetch(url, init)
    .then((res) => {
      if (!res.ok) {
        return res.text().then((text) => {
          let detail = text.trim();
          try {
            detail = JSON.parse(text)?.detail ?? detail;
          } catch {
            /* ignore */
          }
          onError(detail || `Request failed (${res.status})`);
        });
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let hadError = false;
      let completed = false;
      function markDone() {
        if (completed) return;
        completed = true;
        onDone();
      }

      function read(): Promise<void> {
        if (!reader) return Promise.resolve();
        return reader.read().then(({ done, value }) => {
          if (done) {
            if (!hadError && !completed) {
              markDone();
            }
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          let eventType = "";
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              eventType = line.slice(7);
            } else if (line.startsWith("data: ")) {
              const data = JSON.parse(line.slice(6));
              if (eventType === "progress") {
                onProgress(data.message);
              } else if (eventType === "done") {
                markDone();
              } else if (eventType === "error") {
                hadError = true;
                onError(data.detail || "Research failed — please try again.");
              }
            }
          }
          return read();
        });
      }

      return read();
    })
    .catch((err: unknown) => {
      onError(err instanceof Error ? err.message : "Network error — please try again.");
    });
}
