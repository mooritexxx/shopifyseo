import { useEffect, useMemo, useState } from "react";
import type { ArticleDraftProgressEvent } from "../lib/run-article-draft-stream";

const PHASE_ORDER = ["content", "image", "encode", "body", "shopify", "attach", "local"] as const;

const PHASE_LABELS: Record<string, string> = {
  content: "Generate article content (AI)",
  image: "Featured image — generate & upload",
  encode: "Convert images to WebP (Pillow)",
  body: "Hero image in article body",
  shopify: "Create draft in Shopify",
  attach: "Featured image on article record",
  local: "Save to local database"
};

const STEP_LABELS: Record<string, string> = {
  prepare_brief: "Prepare SEO brief",
  outline: "Generate outline",
  write_sections: "Write section batches",
  faq_schema: "Build FAQ/schema",
  validate_repair: "Validate and repair",
  content_checkpoint: "Save content checkpoint",
  images: "Generate/upload images",
  insert_body_images: "Insert body images",
  shopify: "Create/update Shopify draft",
  attach_featured_image: "Attach featured image",
  local_save: "Save locally"
};

type PhaseUiStatus = "pending" | "active" | "done" | "skipped" | "failed";

type StepRow = {
  id: string;
  label: string;
  status: PhaseUiStatus;
  summary?: string;
  itemDone?: number;
  itemTotal?: number;
  index?: number;
};

function formatDraftElapsed(ms: number): string {
  if (ms < 60000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function buildPhaseStatuses(events: ArticleDraftProgressEvent[]): Record<string, PhaseUiStatus> {
  const map: Record<string, PhaseUiStatus> = {};
  for (const id of PHASE_ORDER) {
    map[id] = "pending";
  }
  for (const e of events) {
    if (!e.phase) continue;
    const st = (e.state || "").toLowerCase();
    if (st === "done") map[e.phase] = "done";
    else if (st === "skipped") map[e.phase] = "skipped";
    else if (st === "failed" || st === "error") map[e.phase] = "failed";
    else if (st) map[e.phase] = "active";
  }
  return map;
}

function stateToStatus(state?: string): PhaseUiStatus {
  const st = (state || "").toLowerCase();
  if (st === "done") return "done";
  if (st === "skipped") return "skipped";
  if (st === "failed" || st === "error") return "failed";
  if (st) return "active";
  return "pending";
}

function buildDynamicSteps(events: ArticleDraftProgressEvent[]): StepRow[] {
  const latestByStep = new Map<string, ArticleDraftProgressEvent>();
  for (const e of events) {
    if (e.step_key) {
      latestByStep.set(e.step_key, e);
    }
  }
  if (!latestByStep.size) return [];

  const total = Math.max(
    0,
    ...Array.from(latestByStep.values()).map((e) => e.step_total || 0)
  );
  const known = Array.from({ length: total || 0 }, (_, i) => i + 1);
  const byIndex = new Map<number, StepRow>();
  for (const n of known) {
    byIndex.set(n, {
      id: `step-${n}`,
      label: `Step ${n}`,
      status: "pending",
      index: n
    });
  }

  for (const [id, e] of latestByStep.entries()) {
    const index = e.step_index || 999;
    byIndex.set(index, {
      id,
      label: e.step_label || STEP_LABELS[id] || id.replaceAll("_", " "),
      status: stateToStatus(e.state),
      summary: e.result_summary,
      itemDone: e.item_done,
      itemTotal: e.item_total,
      index
    });
  }
  return Array.from(byIndex.values()).sort((a, b) => (a.index || 999) - (b.index || 999));
}

export function ArticleDraftProgressPanel(props: {
  events: ArticleDraftProgressEvent[];
  isRunning: boolean;
  runKey: number;
}) {
  const { events, isRunning, runKey } = props;
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!isRunning) {
      setElapsedMs(0);
      return;
    }
    const t0 = performance.now();
    setElapsedMs(0);
    const id = window.setInterval(() => {
      setElapsedMs(Math.floor(performance.now() - t0));
    }, 100);
    return () => {
      window.clearInterval(id);
      setElapsedMs(Math.floor(performance.now() - t0));
    };
  }, [isRunning, runKey]);

  const phaseStatuses = useMemo(() => buildPhaseStatuses(events), [events]);
  const dynamicSteps = useMemo(() => buildDynamicSteps(events), [events]);
  const displayedSteps = useMemo<StepRow[]>(
    () =>
      dynamicSteps.length
        ? dynamicSteps
        : PHASE_ORDER.map((phaseId) => ({
            id: phaseId,
            label: PHASE_LABELS[phaseId] || phaseId,
            status: phaseStatuses[phaseId] || "pending"
          })),
    [dynamicSteps, phaseStatuses]
  );
  const latestMessage = events.length ? events[events.length - 1].message : "";
  const imageProgress = useMemo(() => {
    let total: number | undefined;
    let done: number | undefined;
    for (const e of events) {
      if (typeof e.images_total === "number") total = e.images_total;
      if (typeof e.images_done === "number") done = e.images_done;
    }
    if (total == null) return null;
    return { total, done: done ?? 0 };
  }, [events]);

  if (!isRunning && events.length === 0) {
    return null;
  }

  return (
    <div className="rounded-2xl border border-line bg-white p-4 text-sm text-slate-600">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line/80 pb-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Elapsed</p>
          <p className="mt-0.5 text-2xl font-semibold tabular-nums text-ink">{formatDraftElapsed(elapsedMs)}</p>
        </div>
        <div className="max-w-[16rem] text-right text-xs text-slate-500">
          {imageProgress ? (
            <p className="mb-1 font-medium text-ink">
              Images: {imageProgress.done}/{imageProgress.total} uploaded
            </p>
          ) : null}
          <p>{isRunning ? "Timed in your browser while the pipeline runs." : "Run finished."}</p>
        </div>
      </div>

      <ul className="mt-4 space-y-2.5">
        {displayedSteps.map((step) => {
          const st = step.status || "pending";
          return (
            <li key={step.id} className="flex items-start gap-3">
              <span
                className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                  st === "done"
                    ? "border-[#8eb89a] bg-[#e8f4ec] text-[#255b38]"
                    : st === "skipped"
                      ? "border-line bg-slate-100 text-slate-400"
                      : st === "failed"
                        ? "border-red-200 bg-red-50 text-red-600"
                      : st === "active"
                        ? "border-ocean bg-ocean/10 text-ocean"
                        : "border-line bg-[#f7f9fc] text-slate-300"
                }`}
                aria-hidden
              >
                {st === "done" ? "✓" : st === "skipped" ? "—" : st === "failed" ? "!" : st === "active" ? "●" : ""}
              </span>
              <span className="min-w-0">
                <span
                  className={
                    st === "active"
                      ? "font-semibold text-ink"
                      : st === "done"
                        ? "text-slate-600"
                        : st === "skipped"
                          ? "text-slate-400 line-through decoration-slate-300"
                          : st === "failed"
                            ? "font-semibold text-red-600"
                            : "text-slate-400"
                  }
                >
                  {step.label}
                </span>
                {typeof step.itemDone === "number" && typeof step.itemTotal === "number" ? (
                  <span className="ml-2 text-xs tabular-nums text-slate-400">
                    {step.itemDone}/{step.itemTotal}
                  </span>
                ) : null}
                {step.summary ? (
                  <span className="mt-0.5 block text-xs leading-5 text-slate-500">{step.summary}</span>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>

      {latestMessage ? (
        <p className="mt-4 rounded-xl bg-[#f7f9fc] px-3 py-2 text-xs text-slate-700">
          <span className="font-semibold text-ink">Latest: </span>
          {latestMessage}
        </p>
      ) : null}

      <p className="mt-3 text-xs text-slate-500">Checkpoints are saved as each backend step completes.</p>
    </div>
  );
}
