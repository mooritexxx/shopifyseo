import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { z } from "zod";
import { getJson, postJson } from "../../lib/api";
import { Button } from "../ui/button";

export const taskSchema = z.object({
  id: z.number(), object_type: z.string(), object_handle: z.string(),
  status: z.enum(["detected", "preparing", "failed", "draft_ready", "reviewed", "applied", "monitoring"]),
  evidence: z.object({primary_query: z.string(), suggested_action: z.string(), queries: z.array(z.object({query: z.string(), impressions: z.number(), clicks: z.number(), position: z.number().nullable(), ctr: z.number().nullable()}))}),
  draft: z.record(z.string()), reviewed: z.record(z.string()), error: z.string(), detail_url: z.string()
});
export const taskLabels = {detected:"Detected", preparing:"Preparing draft", failed:"Needs retry", draft_ready:"Draft ready", reviewed:"Reviewed", applied:"Applied", monitoring:"Monitoring"};
export function useOpportunityTasks(kind?:string, handle?:string) {
  const params = kind && handle ? '?' + new URLSearchParams({object_type:kind,object_handle:handle}) : '';
  return useQuery({queryKey:["opportunity-tasks",kind,handle], queryFn:()=>getJson("/api/opportunities/tasks" + params, z.array(taskSchema)), refetchInterval: 4000});
}
export function OpportunityTaskQueue() {
  const tasks = useOpportunityTasks();
  if (tasks.error) return <p role="alert">Could not load SEO tasks: {tasks.error.message}</p>;
  if (!tasks.data?.length) return null;
  return <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4">
    <h2 className="font-semibold">SEO tasks</h2>
    <p className="mb-3 text-sm text-slate-500">One fix per page, with related queries kept together.</p>
    <div className="divide-y divide-slate-100">{tasks.data.map(task=><Link key={task.id} to={task.detail_url} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm hover:text-indigo-700">
      <span className="min-w-0 break-words"><strong>{task.evidence.primary_query}</strong><span className="block text-xs text-slate-500">{task.object_handle} · {task.evidence.queries.length} queries</span></span>
      <span className="rounded-full bg-slate-100 px-3 py-1 text-xs">{taskLabels[task.status]}</span>
    </Link>)}</div>
  </section>;
}
export function OpportunityTaskPanel({kind, handle, draft, onLoad}: {kind:string; handle:string; draft:Record<string,string>; onLoad:(fields:Record<string,string>)=>void}) {
  const tasks = useOpportunityTasks(kind, handle);
  const client = useQueryClient();
  const [error, setError] = useState("");
  const task = tasks.data?.find(t=>t.object_type===kind && t.object_handle===handle);
  const action = useMutation({mutationFn:({id, name, fields}:{id:number;name:string;fields?:Record<string,string>})=>postJson(`/api/opportunities/tasks/${id}/${name}`,taskSchema,fields?{fields}:undefined),
    onSuccess:()=>{setError(""); void client.invalidateQueries({queryKey:["opportunity-tasks"]});}, onError:(e)=>setError(e.message)});
  if (tasks.error) return <p role="alert" className="text-sm text-red-700">Could not load the opportunity task: {tasks.error.message}</p>;
  if (!task) return null;
  const savedDraft = task.status === "reviewed" ? task.reviewed : task.draft;
  const matchesReview = Object.entries(task.reviewed).every(([key,value])=>draft[key]?.trim()===value.trim());
  return <section className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Opportunity fix · {taskLabels[task.status]}</h2><Link to="/opportunities" className="text-indigo-700">Back to inbox</Link></div>
    <p className="mt-2"><strong>{task.evidence.primary_query}</strong> — {task.evidence.suggested_action}</p>
    <details className="mt-2 text-slate-600"><summary className="cursor-pointer">Search evidence · {task.evidence.queries.length} related queries</summary><ul className="mt-2 space-y-1">{task.evidence.queries.map(q=><li key={q.query}>{q.query} · {q.impressions} impressions · {q.clicks} clicks · position {q.position?.toFixed(1) ?? "—"}</li>)}</ul></details>
    {task.status==='preparing' && <p className="mt-3" role="status">Preparing a targeted draft. You can leave this page and return from the inbox.</p>}
    {(task.status==='draft_ready' || task.status==='reviewed') && <>
      <details className="mt-3"><summary className="cursor-pointer font-medium">Compare prepared draft with editor</summary><div className="mt-2 space-y-3">{Object.entries(savedDraft).map(([key,value])=><div key={key}><h3 className="font-medium">{key.replaceAll('_',' ')}</h3><div className="grid gap-2 md:grid-cols-2"><pre className="whitespace-pre-wrap break-words rounded bg-white p-3 text-xs"><strong>Current editor</strong>{'\n'}{draft[key]}</pre><pre className="whitespace-pre-wrap break-words rounded bg-white p-3 text-xs"><strong>{task.status === "reviewed" ? "Reviewed draft" : "Prepared draft"}</strong>{'\n'}{value}</pre></div></div>)}</div></details>
      <p className="mt-3 text-slate-600">Load the prepared fields into the editor, make any changes, then mark them reviewed. Loading replaces those fields in your current draft.</p>
      <div className="mt-3 flex flex-wrap gap-2"><Button variant="secondary" onClick={()=>onLoad(savedDraft)}>{task.status === "reviewed" ? "Load reviewed draft" : "Load prepared draft"}</Button><Button disabled={action.isPending || (task.status==='reviewed' && matchesReview)} onClick={()=>action.mutate({id:task.id,name:'review',fields:draft})}>Mark editor draft reviewed</Button></div>
      {task.status==='reviewed' && <p className="mt-2">{matchesReview?'Ready for Save to Shopify below.':'Your editor has changed. Review it again before saving.'}</p>}
    </>}
    {task.status==='failed' && <><p role="alert" className="mt-3 text-red-700">{task.error}</p><Button className="mt-2" disabled={action.isPending} onClick={()=>action.mutate({id:task.id,name:'retry'})}>Retry preparation</Button></>}
    {task.status==='applied' && <div className="mt-3"><p>Reviewed changes were saved to Shopify.</p><Button className="mt-2" disabled={action.isPending} onClick={()=>action.mutate({id:task.id,name:'monitor'})}>Move to monitoring</Button></div>}
    {task.status==='monitoring' && <p className="mt-3">Follow progress using this page’s performance history after future syncs. Automatic before/after evaluation is not enabled.</p>}
    {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}
  </section>;
}
