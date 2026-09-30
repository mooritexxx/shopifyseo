import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { useQueryClient } from "@tanstack/react-query";

export function useBodyDraftSync<T extends { body_html: string }>(
  resourceKey: string, incoming: string | undefined,
  draft: T, setDraft: Dispatch<SetStateAction<T>>,
  baseline: T, setBaseline: Dispatch<SetStateAction<T>>,
) {
  const key = useRef(resourceKey);
  const qc = useQueryClient();
  useEffect(() => {
    const refresh = () => { void qc.invalidateQueries({ predicate: q => ["product-detail", "collections", "pages", "article"].includes(String(q.queryKey[0])) }); };
    window.addEventListener("focus", refresh);
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("internal-link-body");
    if (channel) channel.onmessage = refresh;
    return () => { window.removeEventListener("focus", refresh); channel?.close(); };
  }, [qc]);
  useEffect(() => {
    if (key.current !== resourceKey) { key.current = resourceKey; return; }
    if (incoming === undefined || incoming === baseline.body_html) return;
    if (draft.body_html === baseline.body_html || draft.body_html === incoming) {
      setDraft(d => ({ ...d, body_html: incoming }));
      setBaseline(d => ({ ...d, body_html: incoming }));
    }
  }, [resourceKey, incoming, draft.body_html, baseline.body_html, setDraft, setBaseline]);
  const conflict = key.current === resourceKey && incoming !== undefined && incoming !== baseline.body_html && draft.body_html !== baseline.body_html && draft.body_html !== incoming;
  const useLatestBody = () => {
    if (incoming !== undefined) {
      setDraft(d => ({ ...d, body_html: incoming }));
      setBaseline(d => ({ ...d, body_html: incoming }));
    }
  };
  return { conflict, useLatestBody };
}
