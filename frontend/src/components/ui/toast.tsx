import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { toast as sonnerToast } from "sonner";

export type ToastVariant = "success" | "error" | "warning" | "info" | "default";

export interface ToastProps {
  children: ReactNode;
  variant?: ToastVariant;
  duration?: number;
  onClose?: () => void;
  customIcon?: ReactNode;
}

const variantToSonner: Record<ToastVariant, "success" | "error" | "warning" | "info" | "message"> = {
  success: "success",
  error: "error",
  warning: "warning",
  info: "info",
  default: "message",
};

export function Toast({ children, variant = "default", duration = 5000, onClose, customIcon }: ToastProps) {
  const idRef = useRef<string | number | undefined>(undefined);
  const childrenRef = useRef(children);
  childrenRef.current = children;

  useEffect(() => {
    const method = variantToSonner[variant];
    const opts: Parameters<typeof sonnerToast>[1] & { id?: string | number } = {
      duration: duration <= 0 ? Infinity : duration,
      onDismiss: onClose,
      onAutoClose: onClose,
      icon: customIcon ?? undefined,
    };

    if (idRef.current !== undefined) {
      opts.id = idRef.current;
      sonnerToast[method](childrenRef.current, opts);
    } else {
      idRef.current = sonnerToast[method](childrenRef.current, opts);
    }
  });

  useEffect(() => {
    return () => {
      if (idRef.current !== undefined) {
        sonnerToast.dismiss(idRef.current);
      }
    };
  }, []);

  return null;
}
