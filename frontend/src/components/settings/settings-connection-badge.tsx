export type ConnectionBadgeTone = "success" | "warning" | "danger" | "neutral";

const toneClasses: Record<ConnectionBadgeTone, { pill: string; dot: string }> = {
  success: {
    pill: "border-green-200 bg-green-50 text-green-700",
    dot: "bg-green-500"
  },
  warning: {
    pill: "border-amber-200 bg-amber-50 text-amber-700",
    dot: "bg-amber-500"
  },
  danger: {
    pill: "border-red-200 bg-red-50 text-red-600",
    dot: "bg-red-500"
  },
  neutral: {
    pill: "border-slate-200 bg-slate-50 text-slate-600",
    dot: "bg-slate-400"
  }
};

export type SettingsConnectionBadgeProps = {
  label: string;
  tone: ConnectionBadgeTone;
};

export function SettingsConnectionBadge({ label, tone }: SettingsConnectionBadgeProps) {
  const c = toneClasses[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${c.pill}`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${c.dot}`} aria-hidden />
      {label}
    </span>
  );
}
