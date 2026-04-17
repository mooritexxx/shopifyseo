import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";

import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Textarea } from "../components/ui/textarea";
import { getJson, postJson } from "../lib/api";

const labContextSchema = z.object({
  google_configured: z.boolean(),
  google_connected: z.boolean(),
  customer_id: z.string(),
  login_customer_id_default: z.string().default(""),
  developer_token_configured: z.boolean(),
  developer_token_source: z.enum(["env", "db", "unset"]).default("unset"),
  lab_hints: z.array(z.string()).default([])
});

const labInvokeResponseSchema = z.object({
  result: z.record(z.unknown()),
  planning: z.object({
    url_customer_id: z.string(),
    login_customer_id: z.string().optional(),
    note: z.string().optional()
  })
});

type RpcMethod =
  | "generateKeywordIdeas"
  | "generateKeywordHistoricalMetrics"
  | "generateKeywordForecastMetrics"
  | "generateAdGroupThemes";

const TAB_META: { id: RpcMethod; label: string; blurb: string }[] = [
  {
    id: "generateKeywordIdeas",
    label: "Keyword ideas",
    blurb:
      "Calls KeywordPlanIdeaService.GenerateKeywordIdeas (same idea data as Keyword Planner). Default: English + US geo + a keyword seed — edit JSON to match Google’s REST examples (urlSeed / siteSeed / keywordAndUrlSeed). If Settings has an MCC, the server picks a client account for the URL and sends login-customer-id for the manager."
  },
  {
    id: "generateKeywordHistoricalMetrics",
    label: "Historical metrics",
    blurb:
      "Historical volumes can be sparse for accounts with little traffic. Uses the same customer resolution as Keyword ideas (MCC → client + login header)."
  },
  {
    id: "generateKeywordForecastMetrics",
    label: "Forecast metrics",
    blurb:
      "Forecast metrics for a hypothetical campaign structure. May return limited data on very new or low-activity accounts."
  },
  {
    id: "generateAdGroupThemes",
    label: "Ad group themes",
    blurb:
      "Requires real ad group resource names under the effective client customer ID (see planning note in the response after any call). Replace REPLACE_WITH_AD_GROUP_ID with an ad group from that account."
  }
];

function defaultBodies(customerId: string): Record<RpcMethod, string> {
  const cid = customerId || "YOUR_CUSTOMER_ID";
  return {
    generateKeywordIdeas: JSON.stringify(
      {
        language: "languageConstants/1000",
        geoTargetConstants: ["geoTargetConstants/2840"],
        includeAdultKeywords: false,
        keywordPlanNetwork: "GOOGLE_SEARCH",
        keywordSeed: {
          keywords: ["coffee shop"]
        }
      },
      null,
      2
    ),
    generateKeywordHistoricalMetrics: JSON.stringify(
      {
        keywords: ["coffee shop", "espresso bar"],
        language: "languageConstants/1000",
        geoTargetConstants: ["geoTargetConstants/2840"],
        keywordPlanNetwork: "GOOGLE_SEARCH"
      },
      null,
      2
    ),
    generateKeywordForecastMetrics: JSON.stringify(
      {
        campaign: {
          keywordPlanNetwork: "GOOGLE_SEARCH",
          languageConstants: ["languageConstants/1000"],
          geoModifiers: [
            {
              geoTargetConstant: "geoTargetConstants/2840",
              bidModifier: 1.0
            }
          ],
          biddingStrategy: {
            manualCpcBiddingStrategy: {
              maxCpcBidMicros: 1000000
            }
          },
          adGroups: [
            {
              biddableKeywords: [
                {
                  maxCpcBidMicros: 1000000,
                  keyword: {
                    text: "coffee shop",
                    matchType: "BROAD"
                  }
                }
              ]
            }
          ]
        }
      },
      null,
      2
    ),
    generateAdGroupThemes: JSON.stringify(
      {
        keywords: ["coffee shop", "espresso"],
        adGroups: [`customers/${cid}/adGroups/REPLACE_WITH_AD_GROUP_ID`]
      },
      null,
      2
    )
  };
}

const LOGIN_CUSTOMER_STORAGE_KEY = "googleAdsLabLoginCustomerId";

const ADS_DOC_LINKS: { label: string; href: string }[] = [
  {
    label: "Generate keyword ideas (official sample + curl)",
    href: "https://developers.google.com/google-ads/api/samples/generate-keyword-ideas"
  },
  {
    label: "Access levels & quotas",
    href: "https://developers.google.com/google-ads/api/docs/api-policy/access-levels"
  }
];

export function GoogleAdsLabPage() {
  const ctxQuery = useQuery({
    queryKey: ["google-ads-lab-context"],
    queryFn: () => getJson("/api/google-ads-lab/context", labContextSchema)
  });

  const [loginCustomerId, setLoginCustomerId] = useState("");
  const [bodies, setBodies] = useState<Record<RpcMethod, string> | null>(null);
  const [active, setActive] = useState<RpcMethod>("generateKeywordIdeas");
  const [output, setOutput] = useState<Record<RpcMethod, string | null>>({
    generateKeywordIdeas: null,
    generateKeywordHistoricalMetrics: null,
    generateKeywordForecastMetrics: null,
    generateAdGroupThemes: null
  });
  const bodiesInit = useRef(false);

  const readyDefaults = useMemo(() => {
    const cid = ctxQuery.data?.customer_id?.trim() || "";
    return defaultBodies(cid);
  }, [ctxQuery.data?.customer_id]);

  useEffect(() => {
    if (!ctxQuery.data) return;
    try {
      const s = sessionStorage.getItem(LOGIN_CUSTOMER_STORAGE_KEY);
      if (s != null && s !== "") {
        setLoginCustomerId(s);
      } else if (ctxQuery.data.login_customer_id_default?.trim()) {
        setLoginCustomerId(ctxQuery.data.login_customer_id_default.trim());
      }
    } catch {
      /* ignore */
    }
  }, [ctxQuery.data]);

  useEffect(() => {
    if (!ctxQuery.data || bodiesInit.current) return;
    bodiesInit.current = true;
    const cid = ctxQuery.data.customer_id?.trim() || "";
    setBodies(defaultBodies(cid));
  }, [ctxQuery.data]);

  const invokeMutation = useMutation({
    mutationFn: async (method: RpcMethod) => {
      const raw = bodies?.[method];
      if (!raw?.trim()) throw new Error("Body is empty");
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw) as unknown;
      } catch {
        throw new Error("Invalid JSON in request body");
      }
      return postJson("/api/google-ads-lab/invoke", labInvokeResponseSchema, {
        rpc_method: method,
        body: typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed : {},
        customer_id: "",
        login_customer_id: loginCustomerId.trim()
      });
    },
    onSuccess: (data, method) => {
      const pl = data.planning;
      const header =
        pl?.note || pl?.url_customer_id
          ? `// ${pl.note || "request path"}\n// url_customer_id=${pl.url_customer_id} login_customer_id=${pl.login_customer_id ?? ""}\n\n`
          : "";
      setOutput((o) => ({
        ...o,
        [method]: header + JSON.stringify(data.result, null, 2)
      }));
    },
    onError: (err: Error, method) => {
      setOutput((o) => ({
        ...o,
        [method]: `Error: ${err.message}`
      }));
    }
  });

  if (ctxQuery.isLoading) {
    return (
      <div className="rounded-[30px] border border-white/70 bg-white/90 p-8 shadow-panel">Loading Google Ads lab…</div>
    );
  }

  if (ctxQuery.error || !ctxQuery.data) {
    return (
      <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">
        {(ctxQuery.error as Error)?.message || "Could not load lab context."}
      </div>
    );
  }

  const ctx = ctxQuery.data;
  const ctxOk = ctx.google_configured && ctx.google_connected && ctx.developer_token_configured && ctx.customer_id;

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Experiments</p>
        <h2 className="mt-2 text-4xl font-bold text-ink">Google Ads keyword planning lab</h2>
        <p className="mt-2 max-w-3xl text-sm text-slate-600">
          These requests use your saved developer token, OAuth access, and customer ID from{" "}
          <strong>Settings → Google Ads</strong>, matching Google’s REST mapping{" "}
          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">POST …/customers/&#123;id&#125;:generateKeywordIdeas</code>. If
          your saved ID is a <strong>manager (MCC)</strong>, the server resolves a <strong>client</strong> account for the URL and sets{" "}
          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">login-customer-id</code> to the manager, as in Google’s curl sample.
        </p>
      </div>

      <Card className="border border-blue-200 bg-[#f0f6ff] p-6">
        <p className="text-sm font-semibold text-ink">Notes</p>
        <ul className="mt-2 space-y-1.5 text-sm text-slate-700">
          {(ctx.lab_hints ?? []).map((h) => (
            <li key={h} className="flex gap-2">
              <span className="text-blue-600" aria-hidden>
                •
              </span>
              <span>{h}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-600">
          {ADS_DOC_LINKS.map((l, i) => (
            <span key={l.href}>
              {i > 0 ? " · " : ""}
              <a href={l.href} target="_blank" rel="noreferrer" className="font-medium text-ocean underline-offset-2 hover:underline">
                {l.label}
              </a>
            </span>
          ))}
        </p>
      </Card>

      <Card className="border-line bg-white p-6">
        <p className="text-sm font-semibold text-ink">Connection</p>
        <ul className="mt-2 list-inside list-disc text-sm text-slate-600">
          <li>Google OAuth: {ctx.google_connected ? "connected" : "not connected"}</li>
          <li>OAuth client configured: {ctx.google_configured ? "yes" : "no"}</li>
          <li>Developer token in settings: {ctx.developer_token_configured ? "yes" : "no"}</li>
          <li>
            Developer token source:{" "}
            <span className="font-mono">
              {ctx.developer_token_source === "env"
                ? "environment (GOOGLE_ADS_DEVELOPER_TOKEN)"
                : ctx.developer_token_source === "db"
                  ? "database (Settings)"
                  : "unset"}
            </span>
          </li>
          <li>
            Customer ID: {ctx.customer_id ? <span className="font-mono">{ctx.customer_id}</span> : "(not set)"}
          </li>
          <li>
            Default login-customer-id (Settings):{" "}
            {ctx.login_customer_id_default ? (
              <span className="font-mono">{ctx.login_customer_id_default}</span>
            ) : (
              "(not set — optional for MCC → client)"
            )}
          </li>
        </ul>
        {!ctxOk ? (
          <p className="mt-3 text-sm text-amber-800">
            Configure <strong>Settings → Data Sources → Google Ads</strong> (and connect Google) before requests will succeed.
          </p>
        ) : null}
        <div className="mt-4 max-w-md space-y-2">
          <label className="text-xs font-semibold uppercase tracking-wide text-slate-500" htmlFor="login-customer">
            login-customer-id (overrides Settings default for this page)
          </label>
          <Input
            id="login-customer"
            className="rounded-2xl border border-line bg-white font-mono text-sm"
            placeholder="MCC / manager ID when the customer above is a client under that account"
            value={loginCustomerId}
            onChange={(e) => {
              const v = e.target.value;
              setLoginCustomerId(v);
              try {
                sessionStorage.setItem(LOGIN_CUSTOMER_STORAGE_KEY, v);
              } catch {
                /* ignore */
              }
            }}
          />
          <p className="text-xs text-slate-500">
            Optional. Pre-filled from <strong>Settings → Google Ads → login customer ID</strong> when set; edits here are saved in
            this browser and override that default for lab requests.
          </p>
        </div>
      </Card>

      <Card className="border-line bg-white p-6">
        <p className="text-sm font-semibold text-ink">KeywordPlanIdeaService</p>
        <p className="mb-4 text-sm text-slate-600">
          The response panel includes a short <strong>planning</strong> header (URL customer and login-customer-id) so you can see how
          the request was sent after MCC resolution.
        </p>
        <Tabs value={active} onValueChange={(v) => setActive(v as RpcMethod)} className="space-y-4">
          <TabsList className="flex h-auto min-h-0 flex-wrap gap-2 rounded-[20px] border border-line bg-[#f7f9fc] p-2">
            {TAB_META.map((t) => (
              <TabsTrigger
                key={t.id}
                value={t.id}
                className="rounded-[14px] px-3 py-2 text-left text-xs font-semibold data-[state=active]:bg-white md:text-sm"
              >
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>

          {TAB_META.map((t) => (
            <TabsContent key={t.id} value={t.id} className="mt-0 space-y-3">
              <p className="text-sm text-slate-600">{t.blurb}</p>
              <p className="text-xs text-slate-500">
                REST: <span className="font-mono">POST …/customers/&#123;id&#125;:{t.id}</span>
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setBodies((prev) => ({ ...(prev ?? readyDefaults), [t.id]: readyDefaults[t.id] }))}
                >
                  Reset template
                </Button>
                <Button type="button" onClick={() => invokeMutation.mutate(t.id)} disabled={invokeMutation.isPending || !ctxOk}>
                  {invokeMutation.isPending ? "Calling…" : "Run request"}
                </Button>
              </div>
              <div className="grid gap-4 xl:grid-cols-2">
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Request body (JSON)</p>
                  <Textarea
                    className="min-h-[280px] rounded-2xl border border-line bg-[#fafbfd] font-mono text-xs leading-relaxed"
                    spellCheck={false}
                    value={bodies?.[t.id] ?? ""}
                    onChange={(e) =>
                      setBodies((prev) => ({
                        ...(prev ?? readyDefaults),
                        [t.id]: e.target.value
                      }))
                    }
                  />
                </div>
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Response</p>
                  <Textarea
                    readOnly
                    className="min-h-[280px] rounded-2xl border border-line bg-slate-50 font-mono text-xs leading-relaxed"
                    placeholder="Response JSON appears here."
                    value={output[t.id] ?? ""}
                  />
                </div>
              </div>
            </TabsContent>
          ))}
        </Tabs>
      </Card>
    </div>
  );
}
