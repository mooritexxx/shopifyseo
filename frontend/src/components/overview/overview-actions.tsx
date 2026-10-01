import { type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { getJson } from "../../lib/api";
import { opportunitiesPayloadSchema, articleIdeasPayloadSchema } from "../../types/api";
import { clustersPayloadSchema } from "../../routes/keywords/schemas";
import { rankingsSchema, type RankedKeyword } from "../../hooks/use-rankings";
import { taskSchema, taskLabels } from "../seo/opportunity-task";

const windowSchema = z.object({start:z.string(),end:z.string(),days:z.number(),clicks:z.number(),impressions:z.number(),ctr:z.number().nullable()});
export const resultsSchema = z.object({items:z.array(z.object({id:z.number(),object_handle:z.string(),detail_url:z.string(),applied_at:z.string().nullable(),applied_date:z.string().nullable(),state:z.enum(["unknown_date","waiting","ready","insufficient"]),before:windowSchema.nullable(),after:windowSchema.nullable()})),window_days:z.number(),reporting_lag_days:z.number()});
const number = (n:number) => n.toLocaleString();
const dateLabel = (value:string) => value.slice(0,10);
const detailUrl = (kind:string,handle:string) => `/${({product:"products",collection:"collections",page:"pages",blog_article:"articles"} as Record<string,string>)[kind] ?? "products"}/${kind==="blog_article"?handle.split('/').map(encodeURIComponent).join('/'):encodeURIComponent(handle)}`;

export function rankMovement(item: RankedKeyword) {
  const latest = item.latest;
  const prior = item.trend.filter(c => c.id !== latest?.id);
  const previous = prior[prior.length-1];
  if (!latest || !previous || [latest,previous].some(c=>c.status!=="ok" || c.cancelled)) return null;
  const knownOutside = (c:typeof latest) => c.position !== null ? c.position>10 : c.coverage_complete===1 && c.checked_depth>=10;
  if (latest.position!==null && latest.position<=10 && knownOutside(previous)) return {label:"Entered top 10",gain:true,weight:1000};
  if (previous.position!==null && previous.position<=10 && knownOutside(latest)) return {label:"Left top 10",gain:false,weight:1000};
  if (latest.position===null || previous.position===null || latest.position===previous.position) return null;
  const delta = previous.position-latest.position;
  return {label:`${delta>0?"Up":"Down"} ${Math.abs(delta)} · #${latest.position}`,gain:delta>0,weight:Math.abs(delta)};
}

function Panel({title,description,href,linkLabel,loading,error,retry,empty,children}:{title:string;description:string;href:string;linkLabel:string;loading:boolean;error:Error|null;retry:()=>unknown;empty?:string;children?:ReactNode}) {
  return <section className="overview-panel p-5 min-w-0">
    <div className="overview-section-heading"><h3 className="overview-section-title">{title}</h3><Link className="text-sm font-medium text-indigo-700" to={href}>{linkLabel} →</Link></div>
    <p className="mt-2 text-xs text-slate-500">{description}</p>
    {loading?<p className="mt-4 text-sm text-slate-500" role="status">Loading {title.toLowerCase()}…</p>:error?<div className="mt-4 text-sm" role="alert">Could not load this section. <button className="text-indigo-700 underline" onClick={()=>retry()}>Retry {title.toLowerCase()}</button></div>:empty?<p className="mt-4 text-sm text-slate-500">{empty}</p>:children}
  </section>;
}

export function OverviewActions() {
  const opportunities = useQuery({queryKey:["overview-opportunities"],queryFn:()=>getJson("/api/opportunities?limit=50&sort_by=opportunity_score&sort_dir=desc",opportunitiesPayloadSchema)});
  const rankings = useQuery({queryKey:["rankings"],queryFn:()=>getJson("/api/rankings",rankingsSchema)});
  const tasks = useQuery({queryKey:["opportunity-tasks",undefined,undefined],queryFn:()=>getJson("/api/opportunities/tasks",z.array(taskSchema)),refetchInterval:30000});
  const clusters = useQuery({queryKey:["keyword-clusters"],queryFn:()=>getJson("/api/keywords/clusters",clustersPayloadSchema)});
  const ideas = useQuery({queryKey:["overview-article-ideas"],queryFn:()=>getJson("/api/article-ideas",articleIdeasPayloadSchema)});
  const results = useQuery({queryKey:["overview-change-results"],queryFn:()=>getJson("/api/overview/change-results",resultsSchema),refetchInterval:60000});
  const seen = new Set<string>();
  const top = (opportunities.data?.items ?? []).filter(x=>{const key=x.object_type+':'+x.object_handle;if(seen.has(key))return false;seen.add(key);return true;}).slice(0,5);
  const movers = (rankings.data?.items ?? []).map(item=>({item,movement:rankMovement(item)})).filter(x=>x.movement).sort((a,b)=>b.movement!.weight-a.movement!.weight).slice(0,5);
  const queue = tasks.data ?? [];
  const priority = {failed:0,draft_ready:1,reviewed:2,detected:3,preparing:4,applied:5,monitoring:6};
  const nextTasks = [...queue].sort((a,b)=>priority[a.status]-priority[b.status]).slice(0,5);
  const gaps = (clusters.data?.clusters ?? []).filter(c=>!c.suggested_match || ["high","medium"].includes(c.cannibalization_risk.toLowerCase())).sort((a,b)=>b.priority_score-a.priority_score || b.total_volume-a.total_volume).slice(0,5);
  return <section id="overview-actions" className="space-y-4" aria-label="SEO action dashboard">
    <div><h2 className="overview-section-title">Your next SEO moves</h2><p className="mt-1 text-sm text-slate-500">Latest stored research and workflow activity · independent of the reporting filters above.</p></div>
    <div className="overview-action-grid">
      <div className="min-w-0 space-y-4">
<Panel title="Top SEO opportunities" description="Highest-scored query for each page among the top 50 cached opportunities. Scores are prioritization estimates." href="/opportunities" linkLabel="Open inbox" loading={opportunities.isPending} error={opportunities.error} retry={opportunities.refetch} empty={!top.length?"No qualifying opportunities cached. Review the inbox after your next Search Console sync.":undefined}>
        <ul className="overview-action-list">{top.map(o=><li key={o.id}><Link to={detailUrl(o.object_type,o.object_handle)}>{o.query}</Link><span className="overview-action-badge">Score {Math.round(o.opportunity_score)}</span><p>{o.suggested_action}</p><p>{number(o.impressions)} impressions · {(o.ctr*100).toFixed(1)}% CTR · position {o.position.toFixed(1)}</p><p>{o.object_handle}</p></li>)}</ul>
      </Panel>
<Panel title="Content coverage gaps" description="Unmatched clusters and potential keyword overlap, ordered by planning priority and stored search volume." href="/keywords?tab=clusters" linkLabel="All clusters" loading={clusters.isPending} error={clusters.error} retry={clusters.refetch} empty={!gaps.length?"No unmatched or flagged clusters in the stored research. Generate or review clusters in Keyword Research.":undefined}>
        <ul className="overview-action-list">{gaps.map(c=><li key={c.id}><Link to={`/keywords/clusters/${c.id}`}>{c.name}</Link><span className="overview-action-badge">{!c.suggested_match?'No matched page':'Review overlap'}</span><p>{number(c.total_volume)} estimated monthly searches · {c.keyword_count} keywords{['high','medium'].includes(c.cannibalization_risk.toLowerCase())?` · ${c.cannibalization_risk} overlap risk`:''}</p>{ideas.isPending?<p>Loading related ideas…</p>:ideas.error?<p>Related ideas unavailable. <Link to="/article-ideas">Open Article Ideas →</Link></p>:<div>{(ideas.data?.items.filter(i=>i.linked_cluster_id===c.id).slice(0,2) ?? []).map(i=><p key={i.id}><Link to={`/article-ideas/${i.id}`}>{i.suggested_title} · {i.status}</Link></p>)}{!ideas.data?.items.some(i=>i.linked_cluster_id===c.id)&&<p>No linked article ideas yet.</p>}</div>}</li>)}</ul>
      </Panel>
      </div>
      <div className="min-w-0 space-y-4">
<Panel title="Ranking gains and losses" description="Latest two checks per keyword. Failed, stopped and unverified checks are excluded from comparisons." href="/rankings" linkLabel="View rankings" loading={rankings.isPending} error={rankings.error} retry={rankings.refetch} empty={!movers.length?"No verified ranking changes to show. Two comparable checks are needed.":undefined}>
        <ul className="overview-action-list">{movers.map(({item,movement})=><li key={item.id}><Link to="/rankings">{item.term}</Link><span className={`overview-action-badge ${movement!.gain?'text-emerald-700':'text-rose-700'}`}>{movement!.label}</span><p>Checked {dateLabel(item.latest!.checked_at)}{item.target_mismatch?' · Different URL ranking than target':''}</p></li>)}</ul>
      </Panel>
<Panel title="SEO work queue" description="Review and retry tasks first. Open a page to continue its fix; publishing still happens in the editor." href="/opportunities" linkLabel="All tasks" loading={tasks.isPending} error={tasks.error} retry={tasks.refetch} empty={!queue.length?"No fixes in progress. Prepare a fix from the Opportunity Inbox to start.":undefined}>
        <div className="mt-4 flex flex-wrap gap-2">{Object.entries(taskLabels).map(([status,label])=><span className="overview-action-badge" key={status}>{label}: {queue.filter(t=>t.status===status).length}</span>)}</div>
        <ul className="overview-action-list">{nextTasks.map(t=><li key={t.id}><Link to={t.detail_url}>{t.evidence.primary_query}</Link><span className="overview-action-badge">{taskLabels[t.status]}</span><p>{t.object_handle}</p>{t.status==='failed'&&<p className="text-rose-700">Preparation failed · open the task to review and retry.</p>}</li>)}</ul>
      </Panel>
<Panel title="Results after SEO changes" description="Latest 10 applied fixes: 14 days before vs 14 days after a confirmed opportunity fix. The save day is excluded; allow 3 days for reporting. Changes show association, not proof of causation." href="/opportunities" linkLabel="Review changes" loading={results.isPending} error={results.error} retry={results.refetch} empty={!results.data?.items.length?"No applied opportunity fixes yet. Future confirmed saves will establish a comparison date.":undefined}>
      <ul className="overview-action-list">{results.data?.items.map(r=><li key={r.id}><Link to={r.detail_url}>{r.object_handle}</Link><span className="overview-action-badge">{r.state==='ready'?'Comparison available':r.state==='waiting'?'Waiting for reporting window':r.state==='unknown_date'?'Save date unavailable':'Insufficient history'}</span>{r.applied_date&&<p>Applied {r.applied_date} · Pacific time</p>}{r.before&&r.after&&<p>Before: {r.before.start} – {r.before.end} · After: {r.after.start} – {r.after.end}</p>}{r.state==='ready'&&r.before&&r.after?<div className="overview-result-metrics">{[['Clicks',number(r.before.clicks),number(r.after.clicks)],['Impressions',number(r.before.impressions),number(r.after.impressions)],['CTR',((r.before.ctr ?? 0)*100).toFixed(2)+'%',((r.after.ctr ?? 0)*100).toFixed(2)+'%']].map(([label,before,after])=><p key={label}><span>{label}</span><strong>{before} → {after}</strong></p>)}</div>:<p>{r.state==='unknown_date'?'This older task has no recorded save date; no comparison is inferred.':`Stored days: ${r.before?.days ?? 0}/14 before, ${r.after?.days ?? 0}/14 after. Missing days are not treated as zero traffic.`}</p>}</li>)}</ul>
    </Panel>
      </div>
    </div>
  </section>;
}
