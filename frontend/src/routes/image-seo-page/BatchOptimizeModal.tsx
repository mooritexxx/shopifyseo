import { Check, CircleAlert, Loader2 } from "lucide-react";

import { Button } from "../../components/ui/button";
import { Modal } from "../../components/ui/modal";
import { cn } from "../../lib/utils";
import type { CatalogImageSeoRow } from "../../types/api";
import { BATCH_CONCURRENCY } from "./utils";

export type BatchItemStatus = "pending" | "running" | "success" | "error";
export type BatchEntry = { row: CatalogImageSeoRow; status: BatchItemStatus; message?: string };

interface BatchOptimizeModalProps {
  open: boolean;
  batchQueue: BatchEntry[];
  batchRunning: boolean;
  batchDone: number;
  batchErrors: number;
  batchTotal: number;
  batchFinished: boolean;
  onClose: () => void;
  onStart: () => void;
  onStop: () => void;
}

export function BatchOptimizeModal({
  open,
  batchQueue,
  batchRunning,
  batchDone,
  batchErrors,
  batchTotal,
  batchFinished,
  onClose,
  onStart,
  onStop,
}: BatchOptimizeModalProps) {
  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={
        batchFinished
          ? `Batch complete — ${batchDone} of ${batchTotal} optimized`
          : batchRunning
            ? `Optimizing… ${batchDone + batchErrors} of ${batchTotal} done`
            : `Batch Optimize — ${batchTotal} image${batchTotal === 1 ? "" : "s"}`
      }
      description={`Up to ${BATCH_CONCURRENCY} images processed in parallel (serialized per product).`}
      contentClassName="max-h-[min(80vh,640px)] w-[min(560px,94vw)] overflow-hidden p-4 sm:p-4"
    >
      <div className="flex max-h-[min(52vh,400px)] flex-col gap-3 overflow-y-auto pr-1">
        {batchQueue.map((entry) => (
          <div
            key={entry.row.image_row_id}
            className={cn(
              "flex items-center gap-3 rounded-lg border px-3 py-2",
              entry.status === "running"
                ? "border-ocean/40 bg-ocean/5"
                : entry.status === "success"
                  ? "border-emerald-200 bg-emerald-50/50"
                  : entry.status === "error"
                    ? "border-red-200 bg-red-50/50"
                    : "border-border bg-muted/10"
            )}
          >
            <img
              src={entry.row.url}
              alt=""
              className="h-10 w-10 shrink-0 rounded-md border border-border object-cover"
              loading="lazy"
            />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">
                {entry.row.resource_title}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {entry.row.product_handle || entry.row.resource_handle}
                {entry.row.position != null ? ` · #${entry.row.position}` : ""}
              </p>
              {entry.status === "error" && entry.message ? (
                <p className="mt-0.5 truncate text-xs text-red-600">{entry.message}</p>
              ) : null}
            </div>
            <div className="shrink-0">
              {entry.status === "running" ? (
                <Loader2 className="h-5 w-5 animate-spin text-ocean" />
              ) : entry.status === "success" ? (
                <span className="inline-flex items-center justify-center rounded-md p-1 text-emerald-600">
                  <Check className="h-5 w-5" strokeWidth={2.5} />
                </span>
              ) : entry.status === "error" ? (
                <span className="inline-flex items-center justify-center rounded-md p-1 text-red-500">
                  <CircleAlert className="h-5 w-5" strokeWidth={2} />
                </span>
              ) : (
                <span className="inline-block h-5 w-5 rounded-full border-2 border-border" />
              )}
            </div>
          </div>
        ))}
      </div>

      {batchFinished && (
        <div className="mt-3 flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50/80 px-3 py-2 text-sm">
          <Check className="h-5 w-5 shrink-0 text-emerald-600" strokeWidth={2.5} />
          <span className="text-emerald-900">
            {batchDone} succeeded{batchErrors > 0 ? `, ${batchErrors} failed` : ""}
          </span>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
        {batchFinished ? (
          <Button variant="ocean" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" disabled={batchRunning} onClick={onClose}>
              Cancel
            </Button>
            {batchRunning ? (
              <Button variant="outline" onClick={onStop}>
                Stop after current
              </Button>
            ) : (
              <Button variant="ocean" onClick={onStart}>
                Start
              </Button>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
