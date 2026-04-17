/**
 * Sidekick — floating chat on product/collection/page/article detail routes.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { LoaderCircle, MessageCircle, Send, Sparkles } from "lucide-react";
import { z } from "zod";

import { Button } from "../ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "../ui/sheet";
import { ScrollArea } from "../ui/scroll-area";
import { cn, getErrorMessage } from "../../lib/utils";
import { postJson } from "../../lib/api";
import { Textarea } from "../ui/textarea";

export type SidekickResource = "product" | "collection" | "page" | "blog_article";

export type SidekickBinding = {
  resourceType: SidekickResource;
  handle: string;
  getDraft: () => Record<string, string>;
  applyUpdates: (updates: Record<string, string>) => void;
};

type Ctx = {
  setBinding: (binding: SidekickBinding | null) => void;
};

const SidekickContext = createContext<Ctx | null>(null);

/**
 * Register the current detail page so Sidekick can read the live draft and apply field_updates.
 */
export function useSidekickBinding(binding: Omit<SidekickBinding, "getDraft"> & { draftRef: MutableRefObject<Record<string, string>> }) {
  const ctx = useContext(SidekickContext);
  const { resourceType, handle, draftRef, applyUpdates } = binding;

  useEffect(() => {
    if (!ctx) return;
    const full: SidekickBinding = {
      resourceType,
      handle,
      getDraft: () => ({ ...draftRef.current }),
      applyUpdates,
    };
    ctx.setBinding(full);
    return () => ctx.setBinding(null);
  }, [ctx, resourceType, handle, applyUpdates, draftRef]);
}

const sidekickChatResponseSchema = z.object({
  reply: z.string(),
  field_updates: z.record(z.string())
});

type ChatMessage = { role: "user" | "assistant"; content: string };

function parseDetailRoute(pathname: string): { resourceType: SidekickResource; handle: string } | null {
  const p = pathname.replace(/\/$/, "") || "/";
  const pm = p.match(/^\/products\/([^/]+)$/);
  if (pm) return { resourceType: "product", handle: decodeURIComponent(pm[1]) };
  const cm = p.match(/^\/collections\/([^/]+)$/);
  if (cm) return { resourceType: "collection", handle: decodeURIComponent(cm[1]) };
  const pg = p.match(/^\/pages\/([^/]+)$/);
  if (pg) return { resourceType: "page", handle: decodeURIComponent(pg[1]) };
  const art = p.match(/^\/articles\/([^/]+)\/([^/]+)$/);
  if (art) {
    const blog = decodeURIComponent(art[1]);
    const slug = decodeURIComponent(art[2]);
    return { resourceType: "blog_article", handle: `${blog}/${slug}` };
  }
  return null;
}

function fieldLabel(key: string) {
  if (key === "seo_title") return "SEO title";
  if (key === "seo_description") return "SEO description";
  if (key === "body_html") return "Body";
  if (key === "tags") return "Tags";
  return key;
}

export function SidekickProvider({ children }: { children: ReactNode }) {
  const bindingRef = useRef<SidekickBinding | null>(null);
  const [, bump] = useState(0);
  const setBinding = useCallback((b: SidekickBinding | null) => {
    bindingRef.current = b;
    bump((n) => n + 1);
  }, []);

  const ctx = useMemo(() => ({ setBinding }), [setBinding]);

  return (
    <SidekickContext.Provider value={ctx}>
      {children}
      <SidekickPanel bindingRef={bindingRef} />
    </SidekickContext.Provider>
  );
}

function SidekickPanel({ bindingRef }: { bindingRef: MutableRefObject<SidekickBinding | null> }) {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingUpdates, setPendingUpdates] = useState<Record<string, string> | null>(null);

  const route = parseDetailRoute(pathname);
  const binding = bindingRef.current;
  const routeMatchesBinding =
    route && binding && binding.resourceType === route.resourceType && binding.handle === route.handle;

  useEffect(() => {
    if (!open) return;
    setMessages([]);
    setError(null);
    setPendingUpdates(null);
    setInput("");
  }, [route?.resourceType, route?.handle, open]);

  const send = async () => {
    const text = input.trim();
    if (!text || !route) return;
    setError(null);
    setPendingUpdates(null);
    const nextMessages: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);
    try {
      const clientDraft = routeMatchesBinding && binding ? binding.getDraft() : undefined;
      const data = await postJson("/api/sidekick/chat", sidekickChatResponseSchema, {
        resource_type: route.resourceType,
        handle: route.handle,
        messages: nextMessages,
        ...(clientDraft ? { client_draft: clientDraft } : {})
      });
      setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
      const updates = data.field_updates || {};
      if (Object.keys(updates).length) {
        setPendingUpdates(updates);
      }
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const applyPending = () => {
    if (!pendingUpdates || !binding) return;
    binding.applyUpdates(pendingUpdates);
    setPendingUpdates(null);
  };

  if (!route) {
    return null;
  }

  return (
    <>
      <button
        type="button"
        aria-label="Open Sidekick"
        onClick={() => setOpen(true)}
        className={cn(
          "fixed bottom-6 right-6 z-[60] flex h-14 w-14 items-center justify-center rounded-full shadow-lg transition",
          "bg-[linear-gradient(135deg,#4f8cff_0%,#2147b8_100%)] text-white hover:brightness-110",
          routeMatchesBinding ? "ring-2 ring-white/90" : "opacity-80 ring-1 ring-white/40"
        )}
      >
        <MessageCircle size={26} strokeWidth={2} />
      </button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="right"
          className="flex w-full max-w-md flex-col gap-0 rounded-l-[24px] border-white/70 bg-white p-0 shadow-2xl sm:max-w-md"
        >
          <SheetHeader className="flex-row items-center gap-2 border-b border-slate-200/80 px-4 py-3 space-y-0">
            <Sparkles className="text-[#2147b8] shrink-0" size={18} />
            <div className="min-w-0 flex-1">
              <SheetTitle className="text-sm font-semibold text-ink">Sidekick</SheetTitle>
              <SheetDescription className="text-xs text-slate-500">
                {route.resourceType} · {route.handle}
                {!routeMatchesBinding ? " \u2014 open this page in the editor to apply suggestions" : ""}
              </SheetDescription>
            </div>
          </SheetHeader>

          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-3 px-4 py-3">
              {messages.length === 0 ? (
                <p className="rounded-2xl bg-slate-50 px-3 py-2 text-sm text-slate-600">
                  Ask Sidekick for SEO tweaks, rewrites, or shorter meta copy. If suggested fields appear, use <strong>Apply to form</strong> to load them into this page (then save to Shopify when you're happy).
                </p>
              ) : null}
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={cn(
                    "rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap",
                    m.role === "user" ? "ml-6 bg-[#2147b8]/10 text-ink" : "mr-6 bg-slate-100 text-slate-800"
                  )}
                >
                  {m.content}
                </div>
              ))}
              {loading ? (
                <div className="flex items-center gap-2 text-sm text-slate-500">
                  <LoaderCircle className="animate-spin" size={16} />
                  Sidekick is thinking\u2026
                </div>
              ) : null}
              {error ? <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p> : null}
              {pendingUpdates && Object.keys(pendingUpdates).length ? (
                <div className="rounded-2xl border border-emerald-200 bg-emerald-50/90 p-3 text-sm">
                  <p className="font-medium text-emerald-900">Suggested field updates</p>
                  <ul className="mt-2 list-inside list-disc text-emerald-800">
                    {Object.keys(pendingUpdates).map((k) => (
                      <li key={k}>{fieldLabel(k)}</li>
                    ))}
                  </ul>
                  <Button type="button" className="mt-3 w-full" onClick={applyPending} disabled={!routeMatchesBinding}>
                    Apply to form
                  </Button>
                  {!routeMatchesBinding ? (
                    <p className="mt-1 text-xs text-emerald-800/80">Open this item's detail view with the editor to apply.</p>
                  ) : null}
                </div>
              ) : null}
            </div>
          </ScrollArea>

          <div className="border-t border-slate-200/80 p-3">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={routeMatchesBinding ? "Message Sidekick\u2026" : "Message Sidekick (apply needs the editor open)\u2026"}
              className="min-h-[72px] resize-none rounded-xl border-slate-200"
              disabled={loading}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="mt-2 flex justify-end">
              <Button type="button" onClick={() => void send()} disabled={loading || !input.trim()}>
                <Send className="mr-2" size={16} />
                Send
              </Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
