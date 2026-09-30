import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ComponentPropsWithoutRef, ElementRef } from "react";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

import { cn } from "../../lib/utils";

export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef<
  ElementRef<typeof TabsPrimitive.List>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.List> & { scrollable?: boolean }
>(({ className, scrollable = false, ...props }, ref) => {
  const listRef = useRef<ElementRef<typeof TabsPrimitive.List>>(null);
  useImperativeHandle(ref, () => listRef.current!);
  useEffect(() => {
    const list = listRef.current;
    if (!scrollable || !list) return;
    const revealActiveTab = () => {
      list.querySelector<HTMLElement>('[data-state="active"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
    };
    revealActiveTab();
    const selection = new MutationObserver(revealActiveTab);
    selection.observe(list, { subtree: true, attributes: true, attributeFilter: ["data-state"] });
    const size = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(revealActiveTab);
    size?.observe(list);
    return () => { selection.disconnect(); size?.disconnect(); };
  }, [scrollable]);
  return <TabsPrimitive.List
    ref={listRef}
    className={cn(
      "inline-flex h-9 items-center justify-center rounded-lg bg-muted p-1 text-muted-foreground",
      scrollable && "flex h-auto min-w-0 max-w-full justify-start overflow-x-auto p-1.5 [&>button]:shrink-0",
      className
    )}
    {...props}
  />;
});
TabsList.displayName = TabsPrimitive.List.displayName;

export const TabsTrigger = forwardRef<
  ElementRef<typeof TabsPrimitive.Trigger>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium ring-offset-background transition-all",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      "disabled:pointer-events-none disabled:opacity-50",
      "data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow",
      className
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

export const TabsContent = forwardRef<
  ElementRef<typeof TabsPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      "mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      className
    )}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;
