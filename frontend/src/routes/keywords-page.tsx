import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";

import { Card } from "../components/ui/card";
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

export function KeywordsPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<TabId>("seed");

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

  const activeConfig = tabs.find((t) => t.id === activeTab)!;

  const targetKeywordsQuery = useQuery({
    queryKey: ["target-keywords"],
    queryFn: () => getJson("/api/keywords/target", targetPayloadSchema)
  });

  const newKeywordCount = useMemo(
    () => targetKeywordsQuery.data?.items.filter((i) => i.status === "new").length ?? 0,
    [targetKeywordsQuery.data]
  );

  return (
    <div className="space-y-6 pb-8">
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Keywords</p>
        <h2 className="mt-2 text-4xl font-bold text-ink">Keyword list</h2>
        <p className="mt-2 text-sm text-slate-500">
          Manage seed keywords and explore related target keywords for content planning.
        </p>
      </div>

      <Card>
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as TabId)}
          className="space-y-5"
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
                  Open the Target Keywords tab to approve or dismiss each term. Only approved keywords are used when
                  you generate clusters.
                </p>
              </div>
            </div>
          )}

          <TabsList className="grid h-auto w-full gap-2 rounded-[22px] border border-line bg-[#f7f9fc] p-2 md:grid-cols-4">
            {tabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="justify-start rounded-[18px] px-4 py-3 text-left data-[state=active]:bg-white data-[state=active]:shadow-[0_12px_30px_rgba(13,28,64,0.08)] data-[state=inactive]:text-slate-500 data-[state=inactive]:hover:bg-white/70"
              >
                <span className="text-sm font-semibold">{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>

          <div className="rounded-2xl border border-line bg-[#f7f9fc] px-5 py-4">
            <p className="text-xs uppercase tracking-[0.2em] text-slate-500">{activeConfig.label}</p>
            <p className="mt-2 text-sm text-slate-600">{activeConfig.description}</p>
          </div>

          <TabsContent value="seed" className="mt-0">
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
          <TabsContent value="competitors" className="mt-0">
            <CompetitorsPanel
              onOpenSeedKeywordsTab={() => setActiveTab("seed")}
              onOpenTargetKeywordsTab={() => setActiveTab("target")}
            />
          </TabsContent>
          <TabsContent value="target" className="mt-0">
            <TargetKeywordsPanel seedResearchRunning={seedResearchStatus === "running"} />
          </TabsContent>
          <TabsContent value="clusters" className="mt-0">
            <ClustersPanel />
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
