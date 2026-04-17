import { Card, CardContent } from "./card";

export function SummaryCard({ label, value, tone, hint }: { label: string; value: string; tone: string; hint: string }) {
  return (
    <Card className={`rounded-[26px] shadow-panel ${tone}`}>
      <CardContent className="p-5">
        <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</p>
        <strong className="mt-4 block text-4xl font-bold text-ink">{value}</strong>
        <p className="mt-3 text-sm text-slate-600">{hint}</p>
      </CardContent>
    </Card>
  );
}
