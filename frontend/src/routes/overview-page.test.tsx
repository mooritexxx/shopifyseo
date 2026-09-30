import { cloneElement, type ReactElement } from "react";
import { screen, within, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OverviewPage } from "./overview-page";
import { renderWithProviders } from "../test/test-utils";
import { summarySchema } from "../types/api";
import { getJson } from "../lib/api";

vi.mock("../lib/api", () => ({ getJson: vi.fn(), postJson: vi.fn() }));
// Keep real chart rendering while supplying dimensions instead of a JSDOM ResizeObserver.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return { ...actual, ResponsiveContainer: ({children}: {children: ReactElement<{width?: number; height?: number}>}) => cloneElement(children, {width:500,height:280}) };
});

const windowDates = { start_date: "2026-09-01", end_date: "2026-09-30" };
const cache = {label: "Fresh", kind: "fresh", text: "Fetched today"};
const slice = { rows: [{keys:["can"], impressions:100, clicks:3}], error:"", cache };
const summary = summarySchema.parse({
  counts: {products:10, variants:0, images:0, product_metafields:0, collections:2, collection_metafields:0, collection_products:0, pages:3, blogs:1, blog_articles:1},
  metrics: {collections_missing_meta:1, pages_missing_meta:2, products_missing_meta:0, products_thin_body:1, gsc_pages:8, gsc_clicks:17, gsc_impressions:180, ga4_pages:4, ga4_sessions:20, ga4_views:30},
  recent_runs: [],
  gsc_site: {available:true, timezone:"America/Vancouver", period_mode:"rolling_30d", anchor_date:"2026-09-30", current:{...windowDates, clicks:100, impressions:1000, ctr:0.1, position:4}, previous:{...windowDates, clicks:90, impressions:900, ctr:0.1, position:5}, deltas:{clicks_pct:11}, series:[], cache},
  ga4_site: {available:true, timezone:"America/Vancouver", period_mode:"rolling_30d", anchor_date:"2026-09-30", current:{...windowDates, sessions:500, views:800, new_users:250, avg_session_duration:90, bounce_rate:0.4}, previous:null, deltas:{bounce_rate_pp:-10}, series:[], cache},
  indexing_rollup: {total:16,indexed:10,not_indexed:1,needs_review:0,unknown:5,by_type:{}},
  catalog_completion: {
    products:{total:10,meta_complete:10,missing_meta:0,pct_meta_complete:100,thin_body:1},
    collections:{total:2,meta_complete:1,missing_meta:1,pct_meta_complete:50},
    pages:{total:3,meta_complete:1,missing_meta:2,pct_meta_complete:33.3},
    articles:{total:1,meta_complete:1,missing_meta:0,pct_meta_complete:100}
  },
  overview_goals:{gsc_daily_clicks:null,gsc_daily_impressions:null,ga4_daily_sessions:null,ga4_daily_views:null},
  gsc_property_breakdowns:{available:true,period_mode:"rolling_30d",anchor_date:"2026-09-30",window:windowDates,country:slice,device:{...slice,rows:[{keys:["MOBILE"],impressions:100}]},searchAppearance:{...slice,rows:[]},errors:[],error:""},
  gsc_queries:[{keys:["example query"],clicks:3,impressions:100}], gsc_pages:[{keys:["https://example.com/page"],clicks:3,impressions:100}],
  top_pages:[{entity_type:"product",handle:"example",title:"Example product",gsc_clicks:17,gsc_impressions:180,gsc_ctr:0.094,url:"https://example.com/products/example"}]
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(getJson).mockImplementation(async path => path === "/api/site-authority" ? {found:false,history:[],benchmark:{},domain:"example.com"} : summary);
});

describe("Overview workspaces", () => {
  it("keeps actions ahead of reports and opens audience details with the correct tab", async () => {
    const user = userEvent.setup();
    renderWithProviders(<OverviewPage />);
    const attention = await screen.findByRole("heading", {name:"Needs attention"});
    const performance = screen.getByRole("heading", {name:"Performance"});
    expect(attention.compareDocumentPosition(performance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(screen.getByRole("link",{name:"View countries →"}));
    const countries = screen.getByRole("tab",{name:"Countries"});
    expect(countries).toHaveAttribute("aria-selected","true");
    countries.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab",{name:"Devices"})).toHaveAttribute("aria-selected","true");
    expect(screen.getByRole("region",{name:"Devices table, scroll horizontally"})).toHaveAttribute("tabindex","0");
    expect(screen.getByText("Whole site · unaffected by the Search URL filter")).toBeInTheDocument();
  });

  it("preserves Analytics and catalog metrics behind their controls", async () => {
    const user = userEvent.setup();
    renderWithProviders(<OverviewPage />);
    await screen.findByRole("heading", {name:"Needs attention"});
    const sourceTabs = screen.getByRole("tablist", {name:"Performance source"});
    within(sourceTabs).getByRole("tab",{name:"Search"}).focus();
    await user.keyboard("{ArrowRight}");
    expect(within(sourceTabs).getByRole("tab",{name:"Analytics"})).toHaveAttribute("aria-selected","true");
    expect(screen.getByText("New users")).toBeVisible();
    expect(screen.getByText("↓ 10.0 pp vs prior")).toHaveClass("text-emerald-600");
    await user.click(screen.getByText("Synced catalog performance",{exact:false,selector:"summary"}));
    expect(screen.getByRole("link",{name:"Example product"})).toHaveAttribute("href","/products/example");
    expect(screen.getByText(/per-URL sync windows/)).toBeVisible();
    expect(screen.queryByText("Cache",{exact:true})).not.toBeInTheDocument();
    expect(screen.getByRole("button",{name:/No authority data available/})).toBeInTheDocument();
  });

  it("renders previous-period reference lines for Search, CTR and Analytics", async () => {
    vi.mocked(getJson).mockImplementation(async path => path === "/api/site-authority" ? {found:false,history:[],benchmark:{}} : {
      ...summary,
      gsc_site: {...summary.gsc_site,series:[{date:"2026-09-01",clicks:20,impressions:100,ctr_pct:20,position:4}]},
      ga4_site: {...summary.ga4_site,previous:{...windowDates,sessions:300,views:600},series:[{date:"2026-09-01",sessions:20,views:40}]}
    });
    const user = userEvent.setup();
    renderWithProviders(<OverviewPage />);
    const chart = await screen.findByRole("img",{name:/Line chart of daily Search Console clicks/});
    expect(chart.querySelectorAll(".recharts-reference-line-line")).toHaveLength(0);
    await user.click(screen.getByRole("checkbox",{name:"Compare previous-period averages"}));
    expect(chart.querySelectorAll(".recharts-reference-line-line")).toHaveLength(2);
    await user.click(screen.getByRole("tab",{name:"CTR & position"}));
    expect(screen.getByRole("img",{name:/Line chart of daily Search Console CTR/}).querySelectorAll(".recharts-reference-line-line")).toHaveLength(2);
    await user.click(within(screen.getByRole("tablist",{name:"Performance source"})).getByRole("tab",{name:"Analytics"}));
    expect(screen.getByRole("img",{name:/Line chart of daily GA4/}).querySelectorAll(".recharts-reference-line-line")).toHaveLength(2);
  });

  it("does not present an empty prior rollup as comparison history", async () => {
    vi.mocked(getJson).mockImplementation(async path => path === "/api/site-authority" ? {found:false,history:[],benchmark:{}} : {
      ...summary, gsc_site: {...summary.gsc_site, previous: {...windowDates,clicks:0,impressions:0,ctr:0,position:null},deltas:{}}
    });
    renderWithProviders(<OverviewPage />);
    await screen.findByRole("heading",{name:"Needs attention"});
    expect(screen.queryByRole("checkbox",{name:"Compare previous-period averages"})).not.toBeInTheDocument();
    expect(screen.queryByText(/changes versus the previous period/)).not.toBeInTheDocument();
  });

  it("labels comparison averages and preserves the summary request contract", async () => {
    renderWithProviders(<OverviewPage />);
    await screen.findByRole("heading",{name:"Needs attention"});
    fireEvent.click(screen.getByRole("checkbox",{name:"Compare previous-period averages"}));
    expect(screen.getByText(/not daily history/)).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"Collections"}));
    await waitFor(() => expect(vi.mocked(getJson).mock.calls.some(([path])=>path === "/api/summary?gsc_period=rolling_30d&gsc_segment=collections")).toBe(true));
    expect(await screen.findByRole("heading",{name:"Search URLs · Collections"})).toBeVisible();
  });
});
