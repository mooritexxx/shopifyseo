import "./research-page.css";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Plus, RefreshCw, Search, Trash2, Pencil, Square } from "lucide-react";
import { toast } from "sonner";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "../components/ui/dialog";
import {
  estimateRanks,
  useRankActions,
  useRankHistory,
  useRankings,
  type RankCheck,
  type RankedKeyword,
  type RankEstimate,
} from "../hooks/use-rankings";

const date = (value: string) =>
  new Date(value).toLocaleString("en-CA", {
    timeZone: "America/Vancouver",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
export function rankLabel(check: RankCheck | null): string {
  if (!check) return "Not checked";
  if (check.cancelled) return "Stopped · unknown";
  if (check.status === "error") return "Unknown · error";
  if (check.status === "unverified")
    return check.reported_position === null
      ? "Unverified · not found"
      : `Unverified · reported #${check.reported_position}`;
  return check.position === null
    ? check.coverage_complete === 0
      ? `Not found in ${check.pages_checked} ${check.pages_checked === 1 ? "page" : "pages"} checked`
      : `>${check.checked_depth}`
    : `#${check.position}`;
}
function Rank({ check }: { check: RankCheck | null }) {
  const n = check?.status === "ok" ? check.position : null;
  const color =
    n !== null && n !== undefined && n <= 3
      ? "text-emerald-700 bg-emerald-50"
      : n !== null && n !== undefined && n <= 10
        ? "text-amber-700 bg-amber-50"
        : "text-slate-600 bg-slate-100";
  return (
    <span
      title={check?.error || undefined}
      className={`inline-block whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium ${color}`}
    >
      {rankLabel(check)}
    </span>
  );
}
function Trend({
  checks,
  full = false,
}: {
  checks: RankCheck[];
  full?: boolean;
}) {
  const data = checks.map((c) => ({
    time: date(c.checked_at),
    rank: c.status === "ok" ? c.position : null,
  }));
  if (!data.some((d) => d.rank !== null))
    return (
      <span className="whitespace-nowrap text-xs text-slate-400">
        No verified ranks
      </span>
    );
  return (
    <div className={full ? "h-56 w-full" : "h-10 w-28"}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          {full && <CartesianGrid strokeDasharray="3 3" />}
          <XAxis dataKey="time" hide={!full} tick={{ fontSize: 11 }} />
          <YAxis
            reversed
            domain={[1, 50]}
            hide={!full}
            allowDecimals={false}
            width={32}
          />
          <Tooltip />
          <Line
            type="linear"
            dataKey="rank"
            stroke="#2563eb"
            strokeWidth={2}
            dot={full || data.length === 1}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function RankingsPage() {
  const query = useRankings();
  const { save, remove, run, stop } = useRankActions();
  const [stoppedJob, setStoppedJob] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [group, setGroup] = useState("");
  const [sort, setSort] = useState("keyword");
  const [pages, setPages] = useState(5);
  const [edit, setEdit] = useState<{
    term: string;
    target_url: string;
    grp: string;
  } | null>(null);
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState<RankedKeyword | null>(null);
  const [selected, setSelected] = useState<RankedKeyword | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [confirmation, setConfirmation] = useState<{
    estimate: RankEstimate;
    key: string;
  } | null>(null);
  const history = useRankHistory(selected?.id);
  const data = query.data;
  const running = data?.job?.status === "running";
  const stopping = running && (!!data?.job?.cancel_requested || stoppedJob === data?.job?.id || stop.isPending);
  const groups = [
    ...new Set(data?.items.map((k) => k.grp).filter((g): g is string => !!g)),
  ].sort();
  const rows = useMemo(() => {
    const list = (data?.items || []).filter(
      (k) =>
        k.term.includes(filter.toLowerCase()) && (!group || k.grp === group),
    );
    return list.sort((a, b) =>
      sort === "position"
        ? (a.latest?.status === "ok" ? (a.latest.position ?? 999) : 1000) -
            (b.latest?.status === "ok" ? (b.latest.position ?? 999) : 1000) ||
          a.term.localeCompare(b.term)
        : sort === "change"
          ? (b.change ?? -999) - (a.change ?? -999) ||
            a.term.localeCompare(b.term)
          : a.term.localeCompare(b.term),
    );
  }, [data, filter, group, sort]);
  const top10 =
    data?.items.filter(
      (k) =>
        k.latest?.status === "ok" &&
        k.latest.position !== null &&
        k.latest.position <= 10,
    ).length || 0;
  async function prepare(ids?: number[]) {
    setEstimating(true);
    try {
      setConfirmation({
        estimate: await estimateRanks(ids, pages),
        key: crypto.randomUUID(),
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to estimate check");
    } finally {
      setEstimating(false);
    }
  }
  async function stopCheck() {
    if (!data?.job) return;
    const id = data.job.id;
    setStoppedJob(id);
    try {
      await stop.mutateAsync(id);
    } catch (e) {
      setStoppedJob(null);
      toast.error(e instanceof Error ? e.message : "Unable to stop check");
    }
  }
  return (
    <div className="research-page space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="research-title">Rankings</h1>
          <p className="mt-2 text-sm text-slate-500">
            vapely.ca · Google.ca · Toronto · Desktop · English
          </p>
        </div>
        <div className="research-actions">
          <Button
            variant="outline"
            onClick={() => {
              setEditing(false);
              setEdit({ term: "", target_url: "", grp: "" });
            }}
          >
            <Plus className="mr-2 h-4 w-4" />
            Add keyword
          </Button>
          <Button
            disabled={running ? stopping : estimating || !data?.items.length}
            onClick={() => void (running ? stopCheck() : prepare())}
          >
            {running && !stopping ? <Square className="mr-2 h-4 w-4" /> : (
              <RefreshCw className={`mr-2 h-4 w-4 ${stopping ? "animate-spin" : ""}`} />
            )}
            {stopping ? "Stopping…" : running ? "Stop check" : "Check all"}
          </Button>
        </div>
      </header>
      <div className="research-metrics">
        <div className="research-panel">
          <p className="research-metric-label">In the top 10</p>
          <p className="research-metric-value">
            {data ? top10 : "—"}{" "}
            <span className="text-base font-normal text-slate-400">
              of {data?.items.length || 0} keywords
            </span>
          </p>
        </div>
        <div className="research-panel">
          <p className="research-metric-label">Requests this month · PT</p>
          <p className="research-metric-value">
            {data ? data.month_used : "—"}{" "}
            <span className="text-base font-normal text-slate-400">
              / {data?.monthly_budget ?? 250}
            </span>
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Includes retries; {data?.reserved || 0} reserved.{" "}
            <Link to="/settings" className="underline">
              Edit budget
            </Link>
          </p>
        </div>
        <div className="research-panel research-ranking-depth">
          <Label htmlFor="rank-depth">Check depth</Label>
          <select
            id="rank-depth"
            value={pages}
            onChange={(e) => setPages(Number(e.target.value))}
            className="mt-2 block w-full rounded-lg border p-2"
          >
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                Top {n * 10}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-500">
            Stops when found. Estimate includes one retry per page.
          </p>
        </div>
      </div>
      {running && (
        <div
          role="status"
          className="rounded-xl bg-blue-50 p-4 text-sm text-blue-800"
        >
          Checking keywords: {data.job?.completed} of{" "}
          {JSON.parse(data.job?.keyword_ids || "[]").length} processed.{" "}
          {stopping ? "Stopping: waiting for requests already sent to finish. No further requests will be sent." : "You can leave this page while it runs."}
        </div>
      )}
      {data?.job?.status === "cancelled" && (
        <div role="status" className="rounded-xl bg-slate-100 p-4 text-sm text-slate-700">
          Check stopped. Completed results are saved; keywords that had not started keep their previous results.
        </div>
      )}
      {data?.job?.status === "error" && (
        <div
          role="alert"
          className="rounded-xl bg-amber-50 p-4 text-sm text-amber-800"
        >
          {data.job.error}
        </div>
      )}
      <div className="research-panel">
        <div className="research-toolbar mb-3">
          <Input
            aria-label="Search tracked keywords"
            placeholder="Find a keyword…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="max-w-xs"
          />
          <select
            aria-label="Filter by group"
            value={group}
            onChange={(e) => setGroup(e.target.value)}
            className="rounded-lg border p-2 text-sm"
          >
            <option value="">All groups</option>
            {groups.map((g) => (
              <option key={g}>{g}</option>
            ))}
          </select>
          <select
            aria-label="Sort keywords"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            className="rounded-lg border p-2 text-sm"
          >
            <option value="keyword">Keyword A–Z</option>
            <option value="position">Best position</option>
            <option value="change">Biggest improvement</option>
          </select>
        </div>
        <p className="research-scroll-hint">Scroll horizontally to see all ranking details.</p>
        {query.isPending ? (
          <p className="p-8 text-center">Loading rankings…</p>
        ) : query.error ? (
          <p role="alert" className="p-6 text-red-700">
            {query.error.message}
          </p>
        ) : (
          <div className="research-scroll-region" role="region" aria-label="Tracked keyword results" tabIndex={0}>
            <table className="research-table research-ranking-table w-full min-w-[1150px] text-left">
              <thead className="border-b text-xs uppercase text-slate-500">
                <tr>
                  {[
                    "Keyword",
                    "Position",
                    "Change",
                    "Ranking page",
                    "Top competitor",
                    "Last checked · PT",
                    "Trend",
                    "Actions",
                  ].map((h) => (
                    <th key={h} className={h === "Actions" ? "sticky right-0 border-l bg-white p-3" : "p-3"}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((k) => (
                  <tr
                    key={k.id}
                    className="border-b last:border-0 hover:bg-slate-50 cursor-pointer"
                    onClick={() => setSelected(k)}
                  >
                    <td className="min-w-[170px] p-3">
                      <button
                        className="font-medium text-blue-700 hover:underline"
                        onClick={() => setSelected(k)}
                      >
                        {k.term}
                      </button>
                      {k.grp && (
                        <div className="text-xs text-slate-400">{k.grp}</div>
                      )}
                    </td>
                    <td className="p-3">
                      <Rank check={k.latest} />
                    </td>
                    <td
                      className={`p-3 whitespace-nowrap ${(k.change || 0) > 0 ? "text-emerald-700" : (k.change || 0) < 0 ? "text-red-600" : "text-slate-500"}`}
                    >
                      {k.change !== null
                        ? k.change === 0
                          ? "—"
                          : `${k.change > 0 ? "▲" : "▼"} ${Math.abs(k.change)}`
                        : k.movement === "entered"
                          ? "Entered range"
                          : k.movement === "left"
                            ? "Left range"
                            : k.movement === "new"
                              ? "New"
                              : "—"}
                    </td>
                    <td className="max-w-64 p-3">
                      {k.latest?.ranking_url ? (
                        <a
                          href={k.latest.ranking_url}
                          onClick={(e) => e.stopPropagation()}
                          target="_blank"
                          rel="noreferrer"
                          className="block truncate text-blue-700 hover:underline"
                          title={k.latest.ranking_url}
                        >
                          {new URL(k.latest.ranking_url).pathname || "/"}
                        </a>
                      ) : (
                        "—"
                      )}
                      {k.target_mismatch && (
                        <span className="text-xs text-amber-700">
                          Different from target URL
                        </span>
                      )}
                    </td>
                    <td className="p-3 text-xs">{k.top_competitor || "—"}</td>
                    <td className="p-3 text-xs whitespace-nowrap">
                      {k.latest ? date(k.latest.checked_at) : "—"}
                    </td>
                    <td className="p-3">
                      <Trend checks={k.trend} />
                    </td>
                    <td
                      className="sticky right-0 border-l bg-white p-3"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="flex">
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Check ${k.term}`}
                          disabled={running || estimating}
                          onClick={() => void prepare([k.id])}
                        >
                          <Search className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Edit ${k.term}`}
                          onClick={() => {
                            setEditing(true);
                            setEdit({
                              term: k.term,
                              target_url: k.target_url || "",
                              grp: k.grp || "",
                            });
                          }}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Remove ${k.term}`}
                          onClick={() => setRemoving(k)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!rows.length && (
              <p className="p-10 text-center text-slate-500">
                No keywords match. Add a keyword to start tracking.
              </p>
            )}
          </div>
        )}
        <p className="mt-4 text-xs text-slate-500">
          Ranks are organic search snapshots, not GSC averages. Unverified
          imports and failed checks are excluded from changes and charts.
        </p>
      </div>
      <Dialog
        open={!!edit}
        onOpenChange={(open) => {
          if (!open) setEdit(null);
        }}
      >
        <DialogContent className="bg-white">
          <DialogTitle>{editing ? "Edit keyword" : "Add keyword"}</DialogTitle>
          <DialogDescription>
            Add a search term and optionally the page you want to rank.
            Re-adding a removed term restores its history.
          </DialogDescription>
          {edit && (
            <form
              className="space-y-4"
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await save.mutateAsync(edit);
                  setEdit(null);
                  toast.success("Keyword saved");
                } catch (err) {
                  toast.error((err as Error).message);
                }
              }}
            >
              <div>
                <Label htmlFor="rank-term">Keyword</Label>
                <Input
                  id="rank-term"
                  required
                  maxLength={200}
                  disabled={editing}
                  value={edit.term}
                  onChange={(e) => setEdit({ ...edit, term: e.target.value })}
                />
              </div>
              <div>
                <Label htmlFor="rank-target">Target URL (optional)</Label>
                <Input
                  id="rank-target"
                  type="url"
                  placeholder="https://vapely.ca/pages/…"
                  value={edit.target_url}
                  onChange={(e) =>
                    setEdit({ ...edit, target_url: e.target.value })
                  }
                />
              </div>
              <div>
                <Label htmlFor="rank-group">Group (optional)</Label>
                <Input
                  id="rank-group"
                  maxLength={100}
                  placeholder="Brand, money-term, cluster…"
                  value={edit.grp}
                  onChange={(e) => setEdit({ ...edit, grp: e.target.value })}
                />
              </div>
              <Button type="submit" disabled={save.isPending}>
                {save.isPending ? "Saving…" : "Save keyword"}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!removing}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
      >
        <DialogContent className="bg-white">
          <DialogTitle>Remove keyword?</DialogTitle>
          <DialogDescription>
            Stop tracking “{removing?.term}”. Its history is kept, and adding
            the same keyword restores it. Any check already in progress may
            finish.
          </DialogDescription>
          <Button
            disabled={remove.isPending}
            onClick={async () => {
              if (!removing) return;
              try {
                await remove.mutateAsync(removing.id);
                setRemoving(null);
                toast.success("Keyword removed");
              } catch (e) {
                toast.error((e as Error).message);
              }
            }}
          >
            Remove keyword
          </Button>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null);
        }}
      >
        <DialogContent className="bg-white">
          <DialogTitle>Confirm ranking check</DialogTitle>
          <DialogDescription>
            Searches stop early when Vapely is found. The maximum includes one
            retry per page.
          </DialogDescription>
          {confirmation && (
            <>
              <div className="rounded-xl bg-slate-50 p-4 text-sm space-y-2">
                <p>
                  {confirmation.estimate.keyword_ids.length} keywords · top{" "}
                  {confirmation.estimate.max_pages * 10}
                </p>
                <p>
                  Up to{" "}
                  <strong>
                    {confirmation.estimate.searches_worst_case} requests
                  </strong>{" "}
                  ({confirmation.estimate.searches_base} before retries)
                </p>
                <p>
                  This month: {confirmation.estimate.month_used} /{" "}
                  {confirmation.estimate.monthly_budget}
                </p>
                <p>
                  SerpApi credits remaining:{" "}
                  {confirmation.estimate.serpapi_remaining}
                </p>
              </div>
              {confirmation.estimate.reason && (
                <p role="alert" className="text-sm text-amber-800">
                  {confirmation.estimate.reason}
                </p>
              )}
              <Button
                disabled={!confirmation.estimate.allowed || run.isPending}
                onClick={async () => {
                  try {
                    await run.mutateAsync({
                      keyword_ids: confirmation.estimate.keyword_ids,
                      max_pages: confirmation.estimate.max_pages,
                      request_key: confirmation.key,
                    });
                    setConfirmation(null);
                    toast.success("Ranking check started");
                  } catch (e) {
                    toast.error((e as Error).message);
                  }
                }}
              >
                {run.isPending ? "Starting…" : "Start check"}
              </Button>
            </>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto bg-white">
          <DialogTitle>{selected?.term}</DialogTitle>
          <DialogDescription>
            Toronto · Desktop · English. Lower ranks are better. Gaps indicate
            unranked, failed, or unverified checks.
          </DialogDescription>
          {history.isPending ? (
            <p>Loading history…</p>
          ) : history.error ? (
            <p role="alert">{history.error.message}</p>
          ) : (
            <>
              <Trend checks={[...(history.data || [])].reverse()} full />
              <table className="w-full text-sm text-left">
                <thead>
                  <tr>
                    <th className="p-2">Checked · PT</th>
                    <th className="p-2">Position</th>
                    <th className="p-2">Requests</th>
                    <th className="p-2">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data?.map((c) => (
                    <tr className="border-t" key={c.id}>
                      <td className="p-2 whitespace-nowrap">
                        {date(c.checked_at)}
                      </td>
                      <td className="p-2">
                        <Rank check={c} />
                      </td>
                      <td className="p-2">{c.searches_used}</td>
                      <td className="p-2 text-xs">
                        {c.error ||
                          (c.ranking_url ? (
                            <a
                              className="text-blue-700 break-all"
                              href={c.ranking_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {c.ranking_url}
                            </a>
                          ) : (
                            c.coverage_complete === 0
                              ? `Not found in ${c.pages_checked} pages checked. Google returned fewer than 10 organic results on at least one page; full top-range absence is unverified.`
                              : `Not found in top ${c.checked_depth}`
                          ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!history.data?.length && <p>No checks yet.</p>}
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
