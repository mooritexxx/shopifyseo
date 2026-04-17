import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";

import { cn } from "../../lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-[0.1em] transition-colors",
  {
    variants: {
      variant: {
        default:     "bg-primary/10 text-primary border border-primary/20",
        secondary:   "bg-secondary text-secondary-foreground border border-line",
        destructive: "bg-destructive/10 text-destructive border border-destructive/20",
        outline:     "border border-border text-foreground",
        /* Priority variants */
        high:    "bg-[#ffe4db] text-[#a33f17] border border-[#ffcab8]",
        medium:  "bg-[#fff1c7] text-[#8a6500] border border-[#ffe89a]",
        low:     "bg-[#ddf7ef] text-[#0b6b57] border border-[#b5edd9]",
        success: "bg-[#ddf7ef] text-[#0b6b57] border border-[#b5edd9]",
        warning: "bg-[#fff1c7] text-[#8a6500] border border-[#ffe89a]",
        error:   "bg-[#ffe4db] text-[#a33f17] border border-[#ffcab8]"
      }
    },
    defaultVariants: { variant: "default" }
  }
);

const autoVariantMap: Record<string, "high" | "medium" | "low"> = {
  high: "high", medium: "medium", low: "low"
};

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, children, ...props }: BadgeProps) {
  const resolved =
    variant ??
    (typeof children === "string" ? (autoVariantMap[children.toLowerCase()] ?? "default") : "default");

  return (
    <span className={cn(badgeVariants({ variant: resolved }), className)} {...props}>
      {children}
    </span>
  );
}

export { badgeVariants };
