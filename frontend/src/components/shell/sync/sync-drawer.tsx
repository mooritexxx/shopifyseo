import { useEffect, useState } from "react";
import {
  Activity,
  Box,
  Check,
  ChevronDown,
  ClipboardCopy,
  Database,
  Globe,
  Layers3,
  Play,
  RefreshCw,
  Settings2,
  Sparkles,
  Square,
  X
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { Button } from "../../ui/button";
import { ToggleSwitch } from "../../ui/toggle-switch";
import { cn } from "../../../lib/utils";
import { SYNC_PIPELINE_SUBTITLE, syncServices, syncSelectionSummary, type SyncServiceValue } from "./constants";
import type { PipelineRowModel } from "./pipeline-derive";
import { SyncEventStream } from "./sync-event-stream";
import type { SyncLogLine } from "./use-sync-event-log";

export type SyncDrawerMode = "idle" | "running" | "done" | "error";

const rowIcons: Record<SyncServiceValue, typeof Box> = {
  shopify: Box,
  gsc: Globe,
  ga4: Activity,
  index: Database,
  pagespeed: Sparkles,
  structured: Layers3
};

function HeroRing({
  pct,
  accent,
  label,
  sublabel,
  elapsed,
  eta
}: {
  pct: number;
  accent: string;
  label: string;
  sublabel: string;
  elapsed: string;
  eta: string;
}) {
  const r = 58;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-b from-white/[0.05] to-white/[0.015] p-4">
      <div
        className="pointer-events-none absolute -inset-10 opacity-90"
        style={{
          background: `radial-gradient(circle at 20% 30%, color-mix(in oklab, ${accent} 13%, transparent), transparent 60%)`
        }}
      />
      <div className="relative flex items-center gap-4">
        <div className="relative h-[132px] w-[132px] shrink-0">
          <svg width="132" height="132" viewBox="0 0 132 132" className="-rotate-90" aria-hidden>
            <circle cx="66" cy="66" r={r} stroke="rgba(255,255,255,0.08)" strokeWidth="6" fill="none" />
            <circle
              cx="66"
              cy="66"
              r={r}
              stroke={accent}
              strokeWidth="6"
              fill="none"
              strokeDasharray={c}
              strokeDashoffset={c * (1 - pct / 100)}
              strokeLinecap="round"
              className="transition-[stroke-dashoffset] duration-300"
              style={{ filter: `drop-shadow(0 0 10px color-mix(in oklab, ${accent} 40%, transparent))` }}
            />
          </svg>
          <div className="absolute inset-0 grid place-items-center text-center">
            <div className="text-[30px] font-semibold tabular-nums tracking-tight text-white">
              {Math.round(pct)}
              <span className="ml-0.5 text-sm font-medium text-white/50">%</span>
            </div>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-white/45">Live sync</div>
          <div className="text-balance text-[17px] font-semibold leading-snug text-white">{label}</div>
          <div className="mt-1 text-xs leading-snug text-white/55">{sublabel}</div>
          <div className="mt-3 flex gap-3.5 text-[11px] text-white/60">
            <div>
              <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/35">Elapsed</div>
              <div className="sync-event-stream-mono text-[13px] text-white">{elapsed}</div>
            </div>
            <div className="w-px bg-white/[0.08]" />
            <div>
              <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/35">ETA</div>
              <div className="sync-event-stream-mono text-[13px] text-white">{eta}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SyncScopeSettingsSection({
  settingsExpanded,
  setSettingsExpanded,
  selectedScopes,
  onToggleScope,
  scopeServiceReady,
  scopeHelp,
  forceRefresh,
  onForceRefresh,
  syncRunning,
  className
}: {
  settingsExpanded: boolean;
  setSettingsExpanded: (v: boolean | ((p: boolean) => boolean)) => void;
  selectedScopes: SyncServiceValue[];
  onToggleScope: (v: SyncServiceValue) => void;
  scopeServiceReady: (v: SyncServiceValue) => boolean;
  scopeHelp: (v: SyncServiceValue) => string;
  forceRefresh: boolean;
  onForceRefresh: (v: boolean) => void;
  syncRunning: boolean;
  className?: string;
}) {
  return (
    <div className={cn("text-left", className)}>
      <button
        type="button"
        className="flex w-full items-center justify-between rounded-xl px-1 py-2 text-left text-[11px] font-semibold uppercase tracking-[0.14em] text-white/55 hover:bg-white/[0.06]"
        onClick={() => setSettingsExpanded(!settingsExpanded)}
      >
        Sync settings
        <ChevronDown size={14} className={cn("transition-transform", settingsExpanded ? "rotate-180" : "")} />
      </button>
      {settingsExpanded ? (
        <div className="mt-1 rounded-2xl border border-white/10 bg-white/5 p-3">
          <p className="text-xs uppercase tracking-[0.18em] text-white/45">Services</p>
          <p className="mt-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-white/60">
            {syncSelectionSummary(selectedScopes)}
          </p>
          <div className="mt-3 grid gap-2">
            {syncServices.map((service) => {
              const checked = selectedScopes.includes(service.value);
              const canUse = scopeServiceReady(service.value);
              return (
                <Button
                  key={service.value}
                  type="button"
                  variant="ghost"
                  title={!canUse ? scopeHelp(service.value) : undefined}
                  className={cn(
                    "flex h-auto items-center justify-between rounded-2xl border px-3 py-2 text-sm transition",
                    checked
                      ? "border-[oklch(0.62_0.18_262/0.55)] bg-[oklch(0.62_0.18_262/0.18)] text-white hover:bg-[oklch(0.62_0.18_262/0.22)]"
                      : "border-white/10 bg-white/5 text-white/70 hover:bg-white/10",
                    syncRunning ? "cursor-not-allowed opacity-60" : "",
                    !canUse ? "cursor-not-allowed opacity-45" : ""
                  )}
                  onClick={() => {
                    if (syncRunning || !canUse) return;
                    onToggleScope(service.value);
                  }}
                  disabled={syncRunning || !canUse}
                >
                  <span>{service.label}</span>
                  <span
                    className={cn(
                      "flex h-5 w-5 items-center justify-center rounded-full border",
                      checked ? "border-white/30 bg-white/15" : "border-white/20"
                    )}
                  >
                    {checked ? <Check size={12} /> : null}
                  </span>
                </Button>
              );
            })}
            <ToggleSwitch
              id="seo-sync-force-refresh-drawer"
              className="mt-2"
              label="Force refresh"
              checked={forceRefresh}
              onCheckedChange={onForceRefresh}
              disabled={syncRunning}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StageBar({ pct, color, active }: { pct: number; color: string; active: boolean }) {
  return (
    <div className="relative h-1 overflow-hidden rounded-md bg-white/[0.06]">
      <div className="h-full rounded-md transition-[width] duration-300" style={{ width: `${pct}%`, background: color }} />
      {active && pct < 100 ? (
        <div
          className="pointer-events-none absolute inset-0 sync-drawer-sweep opacity-90"
          style={{
            background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent)"
          }}
        />
      ) : null}
    </div>
  );
}

function StageRow({
  row,
  accent
}: {
  row: PipelineRowModel;
  accent: string;
}) {
  const sub = SYNC_PIPELINE_SUBTITLE[row.key];
  const Icon = rowIcons[row.key];
  const done = row.status === "done";
  const active = row.status === "active";
  const failed = row.status === "failed";
  const iconBg = failed
    ? "rgba(234,96,117,0.14)"
    : done
      ? "rgba(145,239,187,0.12)"
      : active
        ? `color-mix(in oklab, ${accent} 13%, transparent)`
        : "rgba(255,255,255,0.04)";
  const iconColor = failed ? "#ea6075" : done ? "#91efbb" : active ? accent : "rgba(255,255,255,0.4)";

  const rightMain =
    done && row.total > 0
      ? `${row.count || row.total}`
      : active && row.total > 0
        ? `${row.count}/${row.total}`
        : failed
          ? "—"
          : row.total > 0
            ? `0/${row.total}`
            : "—";
  const rightSub = done ? "items" : active ? "syncing" : failed ? "halted" : "queued";

  return (
    <div
      className={cn(
        "grid items-center gap-3 rounded-xl border px-3 py-2.5 transition-[background,border-color] duration-200",
        active ? "border-white/[0.08] bg-white/[0.04]" : "border-transparent"
      )}
      style={{ gridTemplateColumns: "28px 1fr auto" }}
    >
      <div
        className="relative grid h-7 w-7 shrink-0 place-items-center rounded-lg"
        style={{ background: iconBg, color: iconColor }}
      >
        {active ? (
          <svg width="28" height="28" viewBox="0 0 28 28" className="absolute inset-0 -rotate-90" aria-hidden>
            <circle cx="14" cy="14" r="12" stroke="rgba(255,255,255,0.06)" strokeWidth="2" fill="none" />
            <circle
              cx="14"
              cy="14"
              r="12"
              stroke={accent}
              strokeWidth="2"
              fill="none"
              strokeDasharray={2 * Math.PI * 12}
              strokeDashoffset={2 * Math.PI * 12 * (1 - row.pct / 100)}
              strokeLinecap="round"
              className="transition-[stroke-dashoffset] duration-300"
            />
          </svg>
        ) : null}
        <span className="relative">
          {done ? (
            <Check size={14} strokeWidth={2.5} />
          ) : failed ? (
            <span className="text-[13px] font-bold">!</span>
          ) : (
            <Icon size={14} strokeWidth={2} />
          )}
        </span>
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "text-[13px] font-medium tracking-tight",
              active || done ? "text-white" : "text-white/70"
            )}
          >
            {row.label}
          </span>
          {active ? (
            <span
              className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-widest"
              style={{ background: `color-mix(in oklab, ${accent} 20%, transparent)`, color: accent }}
            >
              Running
            </span>
          ) : null}
          {failed ? (
            <span className="rounded bg-[rgba(234,96,117,0.2)] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-widest text-[#ea6075]">
              Failed
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-white/45">{sub}</div>
        {active ? (
          <div className="mt-2">
            <StageBar pct={row.pct} color={accent} active />
          </div>
        ) : null}
      </div>
      <div className="text-right">
        <div
          className={cn(
            "sync-event-stream-mono text-xs font-medium tabular-nums",
            active ? "text-white" : done ? "text-white/75" : failed ? "text-[#ea6075]" : "text-white/35"
          )}
        >
          {rightMain}
        </div>
        <div className="mt-0.5 text-[10px] text-white/35">{rightSub}</div>
      </div>
    </div>
  );
}

export type SyncDrawerProps = {
  accent: string;
  mode: SyncDrawerMode;
  onClose: () => void;
  headerKicker: string;
  headerBadge: string;
  headerBadgeClass: string;
  headerDotColor: string;
  headerDotPulse: boolean;
  runningHero?: {
    pct: number;
    title: string;
    subtitle: string;
    elapsed: string;
    eta: string;
  };
  doneHero?: {
    title: string;
    subtitle: string;
    finishedIn: string;
    relative: string;
  };
  errorHero?: {
    title: string;
    subtitle: string;
    codeLine: string;
  };
  idleHero?: {
    lastRunLine: string;
    serviceCount: number;
  };
  /** Per-entity rows for Shopify catalog + image cache while sync is running. */
  shopifyBreakdown?: { label: string; synced: number; total: number }[];
  pipelineRows: PipelineRowModel[];
  pipelineFraction: string;
  showEventStream: boolean;
  eventLines: SyncLogLine[];
  showChangesGrid: boolean;
  changeCards: { label: string; total: number; sub?: string }[];
  pagespeedErrorDetails: Array<{ object_type: string; handle: string; url: string; error: string }>;
  rawSyncError: string;
  errorSummary: string;
  errorDetails: string | null;
  syncErrorTechnicalOpen: boolean;
  setSyncErrorTechnicalOpen: (o: boolean) => void;
  syncErrorCopied: boolean;
  onCopyError: () => void;
  errorSuggestsSettings: boolean;
  selectedScopes: SyncServiceValue[];
  onToggleScope: (v: SyncServiceValue) => void;
  scopeServiceReady: (v: SyncServiceValue) => boolean;
  scopeHelp: (v: SyncServiceValue) => string;
  forceRefresh: boolean;
  onForceRefresh: (v: boolean) => void;
  syncRunning: boolean;
  onRunSync: () => void;
  canRunSync: boolean;
  runPending: boolean;
  onStopSync: () => void;
  stopPending: boolean;
  onRunBackground: () => void;
  onRunAgain: () => void;
  onRetrySync: () => void;
  cancelRequested: boolean;
};

export function SyncDrawer(props: SyncDrawerProps) {
  const {
    accent,
    mode,
    onClose,
    headerKicker,
    headerBadge,
    headerBadgeClass,
    headerDotColor,
    headerDotPulse,
    runningHero,
    doneHero,
    errorHero,
    idleHero,
    shopifyBreakdown,
    pipelineRows,
    pipelineFraction,
    showEventStream,
    eventLines,
    showChangesGrid,
    changeCards,
    pagespeedErrorDetails,
    rawSyncError,
    errorSummary,
    errorDetails,
    syncErrorTechnicalOpen,
    setSyncErrorTechnicalOpen,
    syncErrorCopied,
    onCopyError,
    errorSuggestsSettings,
    selectedScopes,
    onToggleScope,
    scopeServiceReady,
    scopeHelp,
    forceRefresh,
    onForceRefresh,
    syncRunning,
    onRunSync,
    canRunSync,
    runPending,
    onStopSync,
    stopPending,
    onRunBackground,
    onRunAgain,
    onRetrySync,
    cancelRequested
  } = props;

  const [viewLogOpen, setViewLogOpen] = useState(false);
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  useEffect(() => {
    setViewLogOpen(false);
  }, [mode]);

  const badgeDotStyle = headerDotPulse
    ? {
        background: headerDotColor,
        boxShadow: `0 0 0 4px color-mix(in oklab, ${headerDotColor} 13%, transparent)`
      }
    : { background: headerDotColor };

  return (
    <aside
      data-screen-label="Sync drawer"
      className="flex h-full max-h-[min(100dvh-2rem,900px)] w-full flex-col overflow-hidden rounded-[24px] text-white shadow-[0_30px_80px_-40px_rgba(13,23,43,0.6)] [box-shadow:0_30px_80px_-40px_rgba(13,23,43,0.6),inset_0_0_0_1px_rgba(255,255,255,0.04)] lg:max-h-none lg:rounded-none lg:border-0 lg:border-r lg:border-r-white/[0.08] lg:shadow-none"
      style={{
        background: "linear-gradient(180deg, #111b31 0%, #0d172b 100%)",
        width: "100%",
        maxWidth: 380
      }}
    >
      <div className="flex shrink-0 items-center gap-2.5 border-b border-white/[0.06] px-[18px] py-4">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <div
            className={cn("h-2 w-2 shrink-0 rounded-full", headerDotPulse && "animate-[syncDrawerBlink_1s_ease-in-out_infinite]")}
            style={badgeDotStyle}
          />
          <div className="truncate text-[11px] font-semibold uppercase tracking-[0.22em] text-white/55">{headerKicker}</div>
        </div>
        <div className={cn("shrink-0 rounded-full px-2.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.08em]", headerBadgeClass)}>
          {headerBadge}
        </div>
        <button
          type="button"
          title="Close panel"
          aria-label="Close sync panel"
          onClick={onClose}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border-0 bg-white/[0.06] text-white/65 hover:bg-white/[0.1]"
        >
          <X size={14} strokeWidth={2.25} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-[18px] py-4">
        {mode === "running" && runningHero ? (
          <HeroRing
            pct={runningHero.pct}
            accent={accent}
            label={runningHero.title}
            sublabel={runningHero.subtitle}
            elapsed={runningHero.elapsed}
            eta={runningHero.eta}
          />
        ) : null}

        {mode === "running" && shopifyBreakdown && shopifyBreakdown.length > 0 ? (
          <div className="mt-3 rounded-xl border border-white/[0.07] bg-white/[0.03] px-3 py-2.5">
            <div className="mb-2 text-[9px] font-semibold uppercase tracking-[0.18em] text-white/40">Shopify progress</div>
            <div className="grid gap-1.5">
              {shopifyBreakdown.map((row) => {
                const line =
                  row.total > 0 ? `${row.synced}/${row.total}` : row.synced > 0 ? `${row.synced}` : "—";
                return (
                  <div key={row.label} className="flex items-center justify-between gap-2 text-[11px]">
                    <span className="text-white/55">{row.label}</span>
                    <span className="sync-event-stream-mono tabular-nums text-white/85">{line}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        {mode === "done" && doneHero ? (
          <div className="flex items-center gap-3.5 rounded-2xl border border-[rgba(145,239,187,0.18)] bg-gradient-to-b from-[rgba(145,239,187,0.1)] to-white/[0.015] p-4">
            <div className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-[#91efbb] text-[#0d172b]">
              <Check size={22} strokeWidth={2.5} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-base font-semibold tracking-tight text-white">{doneHero.title}</div>
              <div className="mt-1 text-xs text-white/60">{doneHero.subtitle}</div>
              <div className="mt-2.5 flex flex-wrap gap-3.5 text-[11px] text-white/55">
                <span>
                  Finished in{" "}
                  <span className="sync-event-stream-mono text-white">{doneHero.finishedIn}</span>
                </span>
                <span>·</span>
                <span>{doneHero.relative}</span>
              </div>
            </div>
          </div>
        ) : null}

        {mode === "error" && errorHero ? (
          <div className="flex items-start gap-3.5 rounded-2xl border border-[rgba(234,96,117,0.28)] bg-gradient-to-b from-[rgba(234,96,117,0.16)] to-[rgba(42,20,26,0.3)] p-4">
            <div className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-[#ea6075] text-lg font-bold text-white">!</div>
            <div className="min-w-0 flex-1">
              <div className="text-base font-semibold tracking-tight text-white">{errorHero.title}</div>
              <div className="mt-1 text-xs leading-snug text-[rgba(240,183,193,0.9)]">{errorHero.subtitle}</div>
              {errorHero.codeLine ? (
                <div className="sync-event-stream-mono mt-2.5 rounded-lg border border-[rgba(234,96,117,0.2)] bg-black/25 px-2.5 py-2 text-[10.5px] text-[rgba(255,230,235,0.85)]">
                  <span className="text-[#ea6075]">ERR</span> {errorHero.codeLine}
                </div>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-8 border border-white/15 bg-white/10 px-3 text-xs text-white hover:bg-white/15"
                  onClick={onCopyError}
                >
                  <ClipboardCopy size={14} className="mr-1.5 shrink-0 opacity-80" />
                  {syncErrorCopied ? "Copied" : "Copy error"}
                </Button>
                {errorSuggestsSettings ? (
                  <Button type="button" variant="secondary" size="sm" className="h-8 border border-white/15 bg-white/10 px-0 hover:bg-white/15" asChild>
                    <NavLink to="/settings" className="inline-flex items-center px-3 text-xs text-white">
                      <Settings2 size={14} className="mr-1.5 shrink-0 opacity-80" />
                      Open Settings
                    </NavLink>
                  </Button>
                ) : null}
              </div>
              {errorDetails ? (
                <div className="mt-2">
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-auto w-full justify-between rounded-xl px-2 py-2 text-left text-xs text-white/65 hover:bg-white/10 hover:text-white/85"
                    onClick={() => setSyncErrorTechnicalOpen(!syncErrorTechnicalOpen)}
                  >
                    <span className="uppercase tracking-[0.14em]">Technical details</span>
                    <ChevronDown size={16} className={cn("shrink-0 transition-transform", syncErrorTechnicalOpen ? "rotate-180" : "")} />
                  </Button>
                  {syncErrorTechnicalOpen ? (
                    <pre className="sync-event-stream-mono mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-white/10 bg-black/35 p-2.5 text-[10px] leading-relaxed text-white/70">
                      {errorDetails}
                    </pre>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {mode === "idle" && idleHero ? (
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.035] p-4 text-center">
            <div className="text-base font-semibold text-white">Ready to sync</div>
            <div className="mt-1 text-xs text-white/55">{idleHero.lastRunLine}</div>
            <Button
              type="button"
              onClick={onRunSync}
              disabled={!canRunSync || runPending}
              className="mt-3.5 inline-flex items-center gap-1.5 rounded-[10px] border-0 px-4 py-2.5 text-[13px] font-semibold text-white hover:opacity-95 disabled:opacity-50"
              style={{ background: accent }}
            >
              <Play size={12} className="fill-current" />
              Run sync
            </Button>
          </div>
        ) : null}

        <SyncScopeSettingsSection
          settingsExpanded={settingsExpanded}
          setSettingsExpanded={setSettingsExpanded}
          selectedScopes={selectedScopes}
          onToggleScope={onToggleScope}
          scopeServiceReady={scopeServiceReady}
          scopeHelp={scopeHelp}
          forceRefresh={forceRefresh}
          onForceRefresh={onForceRefresh}
          syncRunning={syncRunning}
          className="mt-[18px] rounded-2xl border border-white/[0.08] bg-white/[0.02] px-3 py-1"
        />

        {/* Pipeline — all modes */}
        <div className="mt-[18px]">
          <div className="mb-2 flex items-center gap-2 px-1">
            <div className="text-[9px] font-semibold uppercase tracking-[0.22em] text-white/40">Pipeline</div>
            <div className="h-px flex-1 bg-white/[0.06]" />
            <div className="sync-event-stream-mono text-[10px] text-white/40">{pipelineFraction}</div>
          </div>
          <div className="mb-2.5 flex gap-0.5">
            {pipelineRows.map((row) => {
              const color =
                row.status === "done"
                  ? "#91efbb"
                  : row.status === "active"
                    ? accent
                    : row.status === "failed"
                      ? "#ea6075"
                      : "rgba(255,255,255,0.08)";
              return (
                <div key={row.key} className="relative h-1 flex-1 overflow-hidden rounded-sm bg-white/[0.06]">
                  <div className="h-full rounded-sm transition-[width] duration-300" style={{ width: `${row.pct}%`, background: color }} />
                  {row.status === "active" ? (
                    <div
                      className="pointer-events-none absolute inset-0 sync-drawer-sweep opacity-90"
                      style={{
                        background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.35), transparent)"
                      }}
                    />
                  ) : null}
                </div>
              );
            })}
          </div>
          <div className="grid gap-0.5">
            {pipelineRows.map((row) => (
              <StageRow key={row.key} row={row} accent={accent} />
            ))}
          </div>
        </div>

        {showEventStream ? (
          <div className="mt-[18px]">
            <SyncEventStream lines={eventLines} accent={accent} />
          </div>
        ) : null}

        {pagespeedErrorDetails.length > 0 ? (
          <div className="mt-4 rounded-2xl border border-[#5c2833] bg-[#2a141a]/70 p-3">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#f0b7c1]">Recent PageSpeed errors</p>
            <div className="mt-2 grid gap-2">
              {pagespeedErrorDetails
                .slice(-3)
                .reverse()
                .map((item) => (
                  <div key={`${item.object_type}:${item.handle}:${item.url}`} className="rounded-xl border border-white/8 bg-white/5 px-3 py-2">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#f0b7c1]">
                      {item.object_type}:{item.handle}
                    </p>
                    <p className="mt-1 break-all text-xs text-white/70">{item.error}</p>
                  </div>
                ))}
            </div>
          </div>
        ) : null}

        {showChangesGrid ? (
          <div className="mt-[18px]">
            <div className="mb-2 px-1 text-[9px] font-semibold uppercase tracking-[0.22em] text-white/40">Changes detected</div>
            <div className="grid grid-cols-2 gap-1.5">
              {changeCards.map((v) => (
                <div key={v.label} className="rounded-[10px] border border-white/[0.06] bg-white/[0.035] p-3">
                  <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.08em] text-white/50">{v.label}</div>
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-xl font-semibold tracking-tight text-white">{v.total}</span>
                    {v.sub ? <span className="sync-event-stream-mono text-[10.5px] text-[#91efbb]">{v.sub}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

      </div>

      <div className="flex shrink-0 flex-col gap-2 border-t border-white/[0.06] bg-black/15 px-[18px] pb-4 pt-3">
        {mode === "running" ? (
          <div className="flex gap-2">
            <Button
              type="button"
              variant="secondary"
              className="flex flex-1 items-center justify-center gap-1.5 rounded-[10px] border-0 bg-white/[0.08] py-2.5 text-[13px] font-medium text-white hover:bg-white/[0.12]"
              onClick={onStopSync}
              disabled={stopPending}
            >
              <Square size={11} className="shrink-0" />
              Stop sync
            </Button>
            <Button
              type="button"
              variant="secondary"
              className="rounded-[10px] border border-white/10 bg-transparent px-3 py-2.5 text-[13px] font-medium text-white/80 hover:bg-white/[0.06]"
              onClick={onRunBackground}
            >
              Run in background
            </Button>
          </div>
        ) : null}
        {mode === "done" ? (
          <div className="flex flex-col gap-2">
            <div className="flex gap-2">
              <Button
                type="button"
                variant="secondary"
                className="flex flex-1 items-center justify-center gap-1.5 rounded-[10px] border-0 bg-white/[0.08] py-2.5 text-[13px] font-medium text-white hover:bg-white/[0.12]"
                onClick={onRunAgain}
                disabled={!canRunSync || runPending}
              >
                <RefreshCw size={12} />
                Run again
              </Button>
              <Button
                type="button"
                variant="secondary"
                className="rounded-[10px] border border-white/10 bg-transparent px-3 py-2.5 text-[13px] font-medium text-white/80 hover:bg-white/[0.06]"
                onClick={() => setViewLogOpen(!viewLogOpen)}
              >
                View log
              </Button>
            </div>
            {viewLogOpen ? (
              <div className="max-h-40 overflow-y-auto rounded-xl border border-white/10 bg-black/35 p-2.5">
                {rawSyncError ? (
                  <pre className="sync-event-stream-mono whitespace-pre-wrap break-words text-[10px] text-white/70">{rawSyncError}</pre>
                ) : eventLines.length ? (
                  <div className="text-[10px] text-white/70">
                    {eventLines.map((l, i) => (
                      <div key={i} className="sync-event-stream-mono border-b border-white/[0.06] py-1 last:border-0">
                        <span className="text-white/40">{l.t}</span>{" "}
                        <span style={{ color: accent }}>{l.tag}</span> {l.msg}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-white/50">No log output for this run.</p>
                )}
              </div>
            ) : null}
          </div>
        ) : null}
        {mode === "error" ? (
          <div className="flex gap-2">
            <Button
              type="button"
              className="flex flex-1 items-center justify-center gap-1.5 rounded-[10px] border-0 py-2.5 text-[13px] font-semibold text-white hover:opacity-95 disabled:opacity-50"
              style={{ background: accent }}
              onClick={onRetrySync}
              disabled={!canRunSync || runPending}
            >
              <RefreshCw size={12} />
              Retry sync
            </Button>
            <Button type="button" variant="secondary" className="rounded-[10px] border border-white/10 bg-transparent px-3 py-2.5 text-[13px] font-medium text-white/80 hover:bg-white/[0.06]" asChild>
              <NavLink to="/settings" className="inline-flex items-center gap-1.5">
                <Settings2 size={12} />
                Fix in Settings
              </NavLink>
            </Button>
          </div>
        ) : null}
        {mode === "idle" ? (
          <Button
            type="button"
            className="flex w-full items-center justify-center gap-1.5 rounded-[10px] border-0 py-2.5 text-[13px] font-semibold text-white hover:opacity-95 disabled:opacity-50"
            style={{ background: accent }}
            onClick={onRunSync}
            disabled={!canRunSync || runPending}
          >
            <Play size={12} className="fill-current" />
            Run sync · {idleHero?.serviceCount ?? selectedScopes.length} service
            {(idleHero?.serviceCount ?? selectedScopes.length) === 1 ? "" : "s"}
          </Button>
        ) : null}
      </div>
      {cancelRequested && syncRunning ? (
        <p className="px-[18px] pb-2 text-center text-xs font-semibold uppercase tracking-[0.16em] text-[#ffcf9f]">Stopping sync…</p>
      ) : null}
    </aside>
  );
}
