import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";


import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { getJson } from "../lib/api";
import { targetPayloadSchema } from "./keywords/schemas";
import { TargetKeywordsPanel } from "./keywords/TargetKeywordsPanel";
import { ClustersPanel } from "./keywords/ClustersPanel";
import { CompetitorsPanel } from "./keywords/CompetitorsPanel";
import { SeedKeywordsPanel } from "./keywords/SeedKeywordsPanel";
import { startKeywordResearchSse } from "./keywords/sse";

const tabs = [
  {
    id: "seed",
    label: "Seed Keywords",
    description: "Core keywords you supply to define your topic clusters."
  },
  {
    id: "competitors",
    label: "Competitors",
    description: "Competitor domains to mine for organic keyword opportunities."
  },
  {
    id: "target",
    label: "Target Keywords",
    description: "Keywords related to your seeds — discovered and prioritised for content."
  },
  {
    id: "clusters",
    label: "Clusters",
    description: "Keywords grouped into topic clusters for content planning."
  },
] as const;

type TabId = (typeof tabs)[number]["id"];

function tabFromQueryParam(raw: string | null): TabId {
  if (raw === "seed" || raw === "competitors" || raw === "target" || raw === "clusters") {
    return raw;
  }
  return "seed";
}

export function KeywordsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const activeTab = useMemo(() => tabFromQueryParam(tabParam), [tabParam]);

  const setActiveTab = useCallback(
    (next: TabId) => {
      setSearchParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          if (next === "seed") {
            p.delete("tab");
          } else {
            p.set("tab", next);
          }
          return p;
        },
        { replace: true }
      );
    },
    [setSearchParams]
  );

  const [seedResearchStatus, setSeedResearchStatus] = useState<"idle" | "running" | "error">("idle");
  const [seedResearchProgress, setSeedResearchProgress] = useState("");
  const [seedResearchError, setSeedResearchError] = useState("");

  function runSeedKeywordResearch() {
    setSeedResearchStatus("running");
    setSeedResearchProgress("");
    setSeedResearchError("");
    startKeywordResearchSse("/api/keywords/target/research", {
      onProgress: setSeedResearchProgress,
      onDone: () => {
        setSeedResearchStatus("idle");
        setSeedResearchProgress("");
        queryClient.invalidateQueries({ queryKey: ["target-keywords"] });
      },
      onError: (detail) => {
        setSeedResearchStatus("error");
        setSeedResearchError(detail);
        setSeedResearchProgress("");
      },
    });
  }


  const targetKeywordsQuery = useQuery({
    queryKey: ["target-keywords"],
    queryFn: () => getJson("/api/keywords/target", targetPayloadSchema)
  });

  const newKeywordCount = useMemo(
    () => targetKeywordsQuery.data?.items.filter((i) => i.status === "new").length ?? 0,
    [targetKeywordsQuery.data]
  );

  return (
    <div className="min-w-0 space-y-5 pb-8">
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Keywords</p>
        <h1 className="mt-1 text-[28px] font-semibold tracking-tight text-ink sm:text-[32px]">Keyword Research</h1>
        <p className="mt-2 text-sm text-slate-500">
          Manage seed keywords and explore related target keywords for content planning.
        </p>
      </div>

      <div className="min-w-0">
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as TabId)}
          className="min-w-0 space-y-4"
        >
          {newKeywordCount > 0 && (
            <div
              className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
              role="status"
            >
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-800" aria-hidden />
              <div>
                <p className="font-medium text-amber-950">
                  {newKeywordCount} keyword{newKeywordCount === 1 ? "" : "s"} waiting for review
                </p>
                <p className="mt-1 text-amber-950/90">
                  Review them in Target Keywords. Approved terms are used to generate clusters.
                </p>
              </div>
            </div>
          )}

          <TabsList scrollable aria-label="Keyword research" className="border border-line bg-slate-100/80">
            {tabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="min-h-10 px-4 text-left data-[state=inactive]:text-slate-600"
              >
                <span className="text-sm font-semibold">{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="seed" className="mt-0 min-w-0">
            <SeedKeywordsPanel
              seedResearchStatus={seedResearchStatus}
              seedResearchProgress={seedResearchProgress}
              seedResearchError={seedResearchError}
              onRunSeedKeywordResearch={runSeedKeywordResearch}
              onDismissSeedResearchError={() => {
                setSeedResearchStatus("idle");
                setSeedResearchError("");
              }}
            />
          </TabsContent>
          <TabsContent value="competitors" className="mt-0 min-w-0">
            <CompetitorsPanel
              onOpenSeedKeywordsTab={() => setActiveTab("seed")}
              onOpenTargetKeywordsTab={() => setActiveTab("target")}
            />
          </TabsContent>
          <TabsContent value="target" className="mt-0 min-w-0">
            <TargetKeywordsPanel seedResearchRunning={seedResearchStatus === "running"} />
          </TabsContent>
          <TabsContent value="clusters" className="mt-0 min-w-0">
            <ClustersPanel />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
