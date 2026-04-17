import { ArrowRight, BookOpen, CheckCircle2, Copy, ExternalLink, Settings } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "../ui/button";
import { cn } from "../../lib/utils";
import type { Summary } from "../../types/api";

const DOCS_README_HREF = "https://github.com/mooritexxx/shopifyseo/blob/main/README.md";

/** Deep-link to Data sources tab (Shopify + Google fields). */
export const SETTINGS_DATA_SOURCES_HREF = "/settings?tab=data-sources";

function catalogEntityTotal(counts: Summary["counts"]) {
  return (
    counts.products +
    counts.collections +
    counts.pages +
    counts.blogs +
    counts.blog_articles
  );
}

/** Granular catalog syncs (CLI) write `sync_runs`; dashboard sync uses `last_dashboard_sync_at` instead. */
function hasLegacyCompletedSyncRun(data: Summary) {
  return data.recent_runs.some((r) => {
    if (!r.finished_at?.trim()) return false;
    if ((r.error_message ?? "").trim()) return false;
    const st = (r.status ?? "").toLowerCase();
    if (st === "failed" || st === "error" || st === "abandoned") return false;
    return true;
  });
}

type StepStatus = "complete" | "current" | "optional" | "pending";

function StepRow({
  step,
  label,
  description,
  status
}: {
  step: number;
  label: string;
  description: string;
  status: StepStatus;
}) {
  const isComplete = status === "complete";
  const isCurrent = status === "current";
  const isOptional = status === "optional";

  return (
    <div
      className={cn(
        "flex gap-4 rounded-2xl border p-4 transition-colors",
        isCurrent && "border-[#5746d9] bg-[#f4f2ff] shadow-[0_2px_16px_rgba(87,70,217,0.12)]",
        !isCurrent && "border-[#e8e4f8] bg-white",
        isOptional && !isCurrent && "border-dashed"
      )}
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center">
        {isComplete ? (
          <CheckCircle2 className="text-emerald-600" size={28} strokeWidth={2} aria-hidden />
        ) : (
          <span
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-full border-2 text-sm font-bold tabular-nums",
              isCurrent ? "border-[#5746d9] bg-white text-[#5746d9]" : "border-slate-200 bg-slate-50 text-slate-500"
            )}
          >
            {step}
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold text-ink">{label}</p>
          {isOptional ? (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
              Optional
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm text-slate-600">{description}</p>
      </div>
    </div>
  );
}

export function overviewShowsOnboarding(data: Summary) {
  if (catalogEntityTotal(data.counts) > 0) return false;
  if ((data.last_dashboard_sync_at ?? "").trim()) return false;
  return !hasLegacyCompletedSyncRun(data);
}

export function OverviewOnboarding({ data }: { data: Summary }) {
  const [copied, setCopied] = useState(false);
  const googleConnected = Boolean(data.gsc_site.available || data.ga4_site.available);

  const oauthRedirect =
    typeof window !== "undefined" ? `${window.location.origin}/auth/google/callback` : "http://127.0.0.1:8000/auth/google/callback";

  const copyRedirect = async () => {
    try {
      await navigator.clipboard.writeText(oauthRedirect);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const step1: StepStatus = "current";
  const step2: StepStatus = "pending";
  const step3: StepStatus = googleConnected ? "complete" : "optional";
  const step4: StepStatus = "pending";

  return (
    <div className="space-y-10 pb-8">
      <header className="rounded-[28px] border border-[#e8e4f8] bg-[linear-gradient(135deg,#faf8ff_0%,#ffffff_55%)] p-8 shadow-[0_8px_32px_rgba(87,70,217,0.08)]">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">First-time setup</p>
        <h1 className="mt-2 text-2xl font-bold tracking-tight text-ink sm:text-3xl">Welcome to Shopify SEO</h1>
        <p className="mt-3 max-w-2xl text-base leading-relaxed text-slate-600">
          Add your Shopify store details and API credentials in <strong className="font-semibold text-[#5746d9]">Settings</strong>
          — nothing is stored in the cloud; everything stays in your local database. Then run a sync from the sidebar to
          import your catalog. Google Search and Analytics are optional and can be connected later.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
          <Button
            asChild
            className="h-12 rounded-xl bg-[#5746d9] px-6 text-base font-semibold text-white hover:bg-[#5746d9]/90"
          >
            <Link to={SETTINGS_DATA_SOURCES_HREF} className="inline-flex items-center gap-2">
              <Settings size={18} strokeWidth={2.25} aria-hidden />
              Add Shopify credentials
            </Link>
          </Button>
          <Button asChild variant="outline" className="h-12 rounded-xl border-[#e8e4f8] px-6 text-base font-semibold">
            <a href="#app-sync-panel" className="inline-flex items-center gap-2">
              Run your first sync
              <ArrowRight size={18} strokeWidth={2.25} aria-hidden />
            </a>
          </Button>
          <a
            className="text-center text-sm font-semibold text-[#5746d9] hover:underline sm:text-left"
            href="#setup-guide"
          >
            Setup guide
          </a>
        </div>
        <p className="mt-4 text-sm text-slate-500">
          After saving Settings, use <span className="font-medium text-slate-700">Sync</span> in the dark sidebar — the
          secondary action above scrolls to it.
        </p>
      </header>

      <section aria-labelledby="setup-steps-heading">
        <h2 id="setup-steps-heading" className="text-lg font-semibold text-ink">
          Setup steps
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Enter Shopify credentials in Settings, then sync. Google integrations are optional.
        </p>
        <div className="mt-5 grid max-w-3xl gap-3">
          <StepRow
            step={1}
            label="Shopify credentials"
            description="Open Settings → Data sources and enter your shop hostname, public storefront domain, API version, custom app Client ID, and Admin API access token. Save when done."
            status={step1}
          />
          <StepRow
            step={2}
            label="Catalog sync"
            description="Run Sync in the sidebar to pull products, collections, pages, and blogs into your local SQLite database."
            status={step2}
          />
          <StepRow
            step={3}
            label="Google Search & Analytics"
            description="Connect Google OAuth in Settings → Data sources for Search Console and GA4 site-level charts (optional)."
            status={step3}
          />
          <StepRow
            step={4}
            label="Overview dashboard"
            description="After sync, return here for indexing, traffic, and catalog SEO completion."
            status={step4}
          />
        </div>
        {!googleConnected ? (
          <p className="mt-4 text-sm text-slate-600">
            Optional next:{" "}
            <Link className="font-semibold text-[#5746d9] hover:underline" to={SETTINGS_DATA_SOURCES_HREF}>
              Open Settings → Data sources
            </Link>
          </p>
        ) : null}
      </section>

      <section id="setup-guide" className="scroll-mt-8">
        <div className="flex items-start gap-3 rounded-[24px] border border-[#e8e4f8] bg-white p-6 shadow-[0_2px_16px_rgba(87,70,217,0.06)]">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#f4f2ff] text-[#5746d9]">
            <BookOpen size={20} strokeWidth={2} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-ink">Open source setup guide</h2>
            <p className="mt-1 text-sm text-slate-600">
              Short checklist for self-hosted installs. More detail is in the{" "}
              <a className="font-semibold text-[#5746d9] hover:underline" href={DOCS_README_HREF} target="_blank" rel="noreferrer">
                README
              </a>
              .
            </p>

            <ol className="mt-4 list-decimal space-y-2 pl-5 text-sm text-slate-700">
              <li>
                Open{" "}
                <Link className="font-semibold text-[#5746d9] hover:underline" to={SETTINGS_DATA_SOURCES_HREF}>
                  Settings → Data sources
                </Link>{" "}
                and fill <span className="font-medium">Store identity</span>, <span className="font-medium">Shopify</span> (shop
                domain, storefront URL, API version, Client ID, Admin API access token), and optionally <span className="font-medium">Google</span>{" "}
                (OAuth client ID and secret). Secret fields are masked; use the eye icon next to each field to reveal values.
              </li>
              <li>
                For Google OAuth in Google Cloud Console, add this <span className="font-medium">Authorized redirect URI</span>:
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <code className="block max-w-full overflow-x-auto rounded-lg border border-[#e8e4f8] bg-[#faf8ff] px-3 py-2 font-mono text-xs text-ink">
                    {oauthRedirect}
                  </code>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="shrink-0 gap-1.5"
                    onClick={() => void copyRedirect()}
                  >
                    <Copy size={14} aria-hidden />
                    {copied ? "Copied" : "Copy"}
                  </Button>
                </div>
              </li>
              <li>
                Click <strong>Save settings</strong>, then use{" "}
                <Link className="font-semibold text-[#5746d9] hover:underline" to={SETTINGS_DATA_SOURCES_HREF}>
                  Connect Google
                </Link>{" "}
                in Settings → Data sources if you want Search Console and GA4. Run <span className="font-medium">Sync</span> in the
                sidebar to import your catalog.
              </li>
            </ol>

            <details className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
              <summary className="cursor-pointer font-semibold text-slate-800">Advanced: environment variables (Docker, CI, headless)</summary>
              <p className="mt-2 leading-relaxed">
                You can inject the same keys via a <code className="rounded bg-white px-1 font-mono text-xs">.env</code> file at the
                repo root or your host&apos;s environment (see <code className="font-mono text-xs">.env.example</code>). The app
                merges them with what you save in Settings; use this for automated deployments where a UI is not practical.
              </p>
            </details>

            <p className="mt-4 text-sm text-slate-600">
              The local database file <code className="font-mono text-xs">shopify_catalog.sqlite3</code> is created in the
              project root when sync runs.
            </p>

            <a
              className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-[#5746d9] hover:underline"
              href={DOCS_README_HREF}
              target="_blank"
              rel="noreferrer"
            >
              Full README on GitHub
              <ExternalLink size={14} aria-hidden />
            </a>
          </div>
        </div>
      </section>

      <section className="rounded-[24px] border border-dashed border-[#e8e4f8] bg-slate-50/80 p-6 text-center">
        <p className="text-sm font-medium text-slate-700">Why is the dashboard empty?</p>
        <p className="mt-2 text-sm text-slate-600">
          Metrics stay hidden until your catalog is imported. Run sync first — zeros everywhere are normal before that.
        </p>
      </section>
    </div>
  );
}
