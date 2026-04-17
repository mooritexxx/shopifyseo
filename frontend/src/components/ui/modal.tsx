import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./dialog";
import { cn } from "../../lib/utils";

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  contentClassName
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  /** Merged onto DialogContent; use for wider layouts (e.g. image preview). */
  contentClassName?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "w-[min(720px,92vw)] rounded-[28px] border border-white/70 bg-white p-6 shadow-panel",
          contentClassName
        )}
      >
        <DialogHeader>
          <DialogTitle className="text-xl font-bold text-ink">{title}</DialogTitle>
          {description && (
            <DialogDescription className="sr-only">{description}</DialogDescription>
          )}
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}
