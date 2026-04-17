import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat("en-CA").format(value ?? 0);
}

export function formatPercent(value: number | null | undefined) {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

/** Human-readable duration from seconds (e.g. session length). */
export function formatDurationSeconds(sec: number | null | undefined) {
  const s = Number(sec);
  if (!Number.isFinite(s) || s <= 0) return "—";
  if (s >= 3600) return `${(s / 3600).toFixed(1)}h`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  if (m === 0) return `${rem}s`;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

/** Strip a dangling " | " or " |" left when the brand suffix is omitted from an SEO title. */
export function cleanSeoTitle(value: string): string {
  return value.replace(/\s*\|\s*$/, "");
}

/** Safely extract a human-readable message from an unknown catch value. */
export function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string" && e) return e;
  return "An unknown error occurred";
}

export function formatRelativeTimestamp(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "Not fetched";

  let date: Date | null = null;
  if (typeof value === "number") {
    date = new Date(value * 1000);
  } else if (/^\d+$/.test(value.trim())) {
    date = new Date(Number(value) * 1000);
  } else {
    date = new Date(value);
  }

  if (Number.isNaN(date.getTime())) return String(value);

  const now = Date.now();
  const diffMs = now - date.getTime();
  const absDiffMs = Math.abs(diffMs);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;

  let relative = "";
  if (absDiffMs < minute) {
    relative = "just now";
  } else if (absDiffMs < hour) {
    relative = `${Math.round(absDiffMs / minute)}m ago`;
  } else if (absDiffMs < day) {
    relative = `${Math.round(absDiffMs / hour)}h ago`;
  } else {
    relative = `${Math.round(absDiffMs / day)}d ago`;
  }

  const exact = new Intl.DateTimeFormat("en-CA", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(date);

  return `${relative} · ${exact}`;
}
