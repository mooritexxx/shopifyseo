import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-60 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:     "bg-primary text-primary-foreground shadow hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline:     "border border-input bg-background shadow-sm hover:bg-muted hover:text-foreground",
        secondary:   "bg-secondary text-secondary-foreground border border-line shadow-sm hover:bg-muted",
        ghost:       "hover:bg-muted hover:text-foreground",
        link:        "text-primary underline-offset-4 hover:underline",
        /* Extra aliases */
        primary:     "bg-primary text-primary-foreground shadow hover:bg-primary/90",
        ocean:       "bg-ocean text-white shadow-panel hover:bg-[#0f4fe0]"
      },
      size: {
        default: "h-9 px-4 py-2",
        sm:      "h-8 rounded-full px-3 text-xs",
        lg:      "h-10 rounded-full px-6",
        icon:    "h-9 w-9 p-0"
      }
    },
    defaultVariants: {
      variant: "default",
      size: "default"
    }
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render the button as its child element — useful for wrapping <Link> */
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { buttonVariants };
