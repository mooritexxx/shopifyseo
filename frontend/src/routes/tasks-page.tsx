import './tasks-page.css';
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Modal } from '../components/ui/modal';
import { Button } from '../components/ui/button';
import { actorNames, statuses, statusNames, riskNames, taskRequest, taskLinkHref, TaskApiError } from '../lib/team-tasks';
import type { TeamTask, TaskFields, TaskLink, TaskEvent, Page } from '../lib/team-tasks';

const dateTime = (value: string) => new Date(value).toLocaleString();
const managers = ['salar', 'chief_of_staff'];
const views = ['Needs you', 'By owner', 'Stale', 'Done this week', 'All tasks'] as const;
const blankLink = (): TaskLink => ({ kind: 'product', id: '', handle: '', blog_handle: '', url: '', label: '' });
const emptyTask = (owner: string): TaskFields => ({ title: '', outcome: '', owner, priority: 'P2', due_on: null, check_by: null, blocked_by: [], blocked_reason: '', links: [], measurement: null, risks: [], authorization: '' });

function ErrorNotice({ error }: { error: unknown }) {
  return error ? <p className="task-error" role="alert">{error instanceof Error ? error.message : 'Something went wrong.'}</p> : null;
}

export function TasksPage() {
  useEffect(() => { sessionStorage.removeItem('task-token'); }, []);
  return <TaskWorkspace />;
}

function TaskWorkspace() {
  const actor = 'salar';
  const session = 'web-salar';
  const [view, setView] = useState<typeof views[number]>('Needs you');
  const [owner, setOwner] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [params, setParams] = useSearchParams();
  const selected = Number(params.get('task')) || null;
  const client = useQueryClient();
  const filters = new URLSearchParams({ limit: '100', offset: String(offset) });
  if (owner) filters.set('owner', owner);
  if (view === 'Needs you') filters.set('status', 'waiting_on_salar');
  else if (view === 'Stale') filters.set('stale', 'true');
  else if (view === 'Done this week') filters.set('done_this_week', 'true');
  else if (status) filters.set('status', status);
  const listing = useQuery({ queryKey: ['team-tasks', session, 'list', filters.toString()], queryFn: () => taskRequest<Page<TeamTask>>( `?${filters}`), refetchInterval: 30000 });
  const creation = useMutation({ mutationFn: (fields: TaskFields) => taskRequest<TeamTask>( '', 'POST', fields), onSuccess: task => { setCreating(false); setParams({ task: String(task.id) }); void client.invalidateQueries({ queryKey: ['team-tasks', session, 'list'] }); } });
  const groups = view === 'By owner' ? Object.keys(actorNames).filter(id => !owner || owner === id) : ['all'];
  return <main className="tasks-workspace">
    <header className="task-header"><div><p className="task-eyebrow">Workspace</p><h1>Tasks</h1><p>Decisions, progress, and proof — shared with your team.</p></div><div className="task-buttons"><span>{actorNames[actor]}</span><Button onClick={() => { setCreating(!creating); creation.reset(); }}>{creating ? 'Cancel new task' : 'New task'}</Button></div></header>
    {creating && <section className="task-panel"><h2>New task</h2><TaskEditor actor={actor} busy={creation.isPending} onSave={fields => creation.mutate(fields)} /><ErrorNotice error={creation.error} /></section>}
    <nav className="task-tabs" aria-label="Task views">{views.map(name => <button type="button" key={name} aria-pressed={view === name} onClick={() => { setView(name); setOffset(0); }}>{name}</button>)}</nav>
    <div className="task-filters"><label>Owner<select value={owner} onChange={e => { setOwner(e.target.value); setOffset(0); }}><option value="">Everyone</option>{Object.entries(actorNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>{(view === 'All tasks' || view === 'By owner') && <label>Status<select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">All statuses</option>{statuses.map(id => <option key={id} value={id}>{statusNames[id]}</option>)}</select></label>}<p>{listing.data?.total ?? '…'} {listing.data?.total === 1 ? 'task' : 'tasks'}{view === 'Stale' ? ' · No log entry for 48 hours' : view === 'Done this week' ? ' · Since Monday, Vancouver time' : ''}</p></div>
    <ErrorNotice error={listing.error} />
    <section className="task-table-scroll" aria-label={view} tabIndex={0}>
      <table className="task-table">
        <thead><tr><th scope="col">Task</th><th scope="col">Priority</th><th scope="col">Owner</th><th scope="col">Status</th><th scope="col">Due / check by</th></tr></thead>
        {groups.map(group => {
          const tasks = (listing.data?.items || []).filter(task => group === 'all' || task.owner === group);
          return <tbody key={group}>
            {group !== 'all' && <tr className="task-group-row"><th colSpan={5} scope="rowgroup">{actorNames[group]} <span>{tasks.length}{listing.data && listing.data.total > 100 ? ' on this page' : ''}</span></th></tr>}
            {tasks.map(task => <tr key={task.id} className="task-table-row" onClick={() => setParams({ task: String(task.id) })}>
              <td><button type="button" className="task-row-title" aria-label={`Open task #${task.id}: ${task.title}`} onClick={() => setParams({ task: String(task.id) })}><span className="task-row-id">#{task.id}</span><span title={task.title}>{task.title}</span></button></td>
              <td>{task.priority}</td><td>{actorNames[task.owner]}</td><td><span className={`task-status status-${task.status}`}>{statusNames[task.status]}</span></td>
              <td>{task.due_on || (task.check_by ? `Check ${task.check_by}` : '—')}</td>
            </tr>)}
            {!tasks.length && <tr><td colSpan={5} className="task-empty">{listing.isPending ? 'Loading tasks…' : view === 'Needs you' ? 'No decisions waiting on Salar.' : view === 'Stale' ? 'No stale tasks.' : 'No tasks in this view.'}</td></tr>}
          </tbody>;
        })}
      </table>
    </section>
    <Modal open={!!selected} onOpenChange={open => { if (!open) setParams({}); }} title={`Task #${selected || ''}`} description="Task details, decisions and history" contentClassName="tasks-workspace task-dialog">
      {selected && <TaskDetail key={selected} id={selected} actor={actor} session={session} />}
    </Modal>
    {listing.data && listing.data.total > 100 && <div className="task-buttons"><Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</Button><span>{offset + 1}–{Math.min(offset + 100, listing.data.total)} of {listing.data.total}</span><Button variant="outline" disabled={offset + 100 >= listing.data.total} onClick={() => setOffset(offset + 100)}>Next</Button></div>}
  </main>;
}

function TaskDetail({ id, actor, session }: { id: number; actor: string; session: string }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [reset, setReset] = useState(0);
  const [conflict, setConflict] = useState(false);
  const [feedback, setFeedback] = useState('');
  const key = ['team-tasks', session, 'detail', id];
  const detail = useQuery({ queryKey: key, queryFn: () => taskRequest<TeamTask>( `/${id}`), refetchOnWindowFocus: false });
  const history = useQuery({ queryKey: ['team-tasks', session, 'events', id, historyOffset], queryFn: () => taskRequest<Page<TaskEvent>>( `/${id}/events?offset=${historyOffset}&limit=30`), refetchOnWindowFocus: false });
  const mutation = useMutation({ mutationFn: ({ endpoint, body }: { endpoint: string; body: unknown }) => taskRequest<TeamTask>( `/${id}${endpoint}`, endpoint ? 'POST' : 'PATCH', body), onSuccess: task => {
    client.setQueryData(key, task); setEditing(false); setFeedback('Saved.'); setReset(value => value + 1); setHistoryOffset(0);
    void client.invalidateQueries({ queryKey: ['team-tasks', session, 'list'] });
    void client.invalidateQueries({ queryKey: ['team-tasks', session, 'events', id] });
  }, onError: error => { setFeedback(''); if (error instanceof TaskApiError && error.status === 409) setConflict(true); } });
  const task = detail.data;
  async function reload() { await detail.refetch(); await history.refetch(); setConflict(false); mutation.reset(); setFeedback('Latest task loaded. Check your input before saving again.'); }
  function save(endpoint: string, body: Record<string, unknown>) { if (task) mutation.mutate({ endpoint, body: { ...body, version: task.version } }); }
  return <aside className="task-panel task-detail" aria-label={`Task ${id}`}><ErrorNotice error={detail.error} />{!task ? <p>Loading task…</p> : <>
    <h2>{task.title}</h2><p>{task.outcome}</p><div className="task-buttons"><span className={`task-status status-${task.status}`}>{statusNames[task.status]}</span><span>{task.priority} · {actorNames[task.owner]}</span></div>
    {task.latest_decision && <section className="task-decision"><h3>Latest manager decision</h3><p>{task.latest_decision.answer}</p><small>{task.latest_decision.actor_label || actorNames[task.latest_decision.actor]}</small><small>{dateTime(task.latest_decision.at)}{task.latest_decision.approved === false ? ' · Not approved' : task.latest_decision.approved ? ' · Approved' : ''}</small></section>}
    {task.question && <section className="task-question"><h3>{task.question}</h3><ul>{task.options.map((option, index) => <li key={index}>{option}</li>)}</ul></section>}
    <dl className="task-facts"><div><dt>Requested by</dt><dd>{actorNames[task.requester]}</dd></div><div><dt>Due / check by</dt><dd>{task.due_on || '—'} / {task.check_by || '—'}</dd></div><div><dt>Last update</dt><dd>{dateTime(task.last_log_at)}</dd></div>{task.risks.length > 0 && <div><dt>Action approval</dt><dd>{task.approval_status.replaceAll('_', ' ')} · {task.risks.map(risk => riskNames[risk]).join(', ')}</dd></div>}</dl>
    {task.authorization && <p><strong>Existing authorization:</strong> {task.authorization}</p>}
    {(task.blocked_reason || task.blocked_by.length > 0) && <section><h3>Blocked by</h3><p>{task.blocked_reason}</p><div className="task-buttons">{task.blocked_by.map(dependency => <a key={dependency} href={`/app/tasks?task=${dependency}`}>Task #{dependency}</a>)}</div></section>}
    {task.links.length > 0 && <section><h3>Links</h3><ul className="task-links">{task.links.map((link, index) => { const href = taskLinkHref(link); const label = link.label || `${link.kind.replaceAll('_', ' ')}: ${link.handle || link.id || link.url}`; return <li key={index}>{href ? <a href={href} target={href.startsWith('http') ? '_blank' : undefined} rel="noreferrer">{label}</a> : <span>{label}</span>}{link.kind === 'opportunity_task' && <small> · Opportunity #{link.id}</small>}</li>; })}</ul></section>}
    {task.measurement && <section><h3>Measurement · {task.measurement.metric}</h3><p>Baseline: {task.measurement.baseline || 'Not recorded'}</p><p>Recheck: {task.measurement.recheck_on || 'Not scheduled'}</p><p>Result: {task.measurement.result || 'Pending'}</p></section>}
    {task.proof && <section className="task-decision"><h3>Completion proof</h3><p>{task.proof}</p></section>}
    <ErrorNotice error={mutation.error} />{conflict && <div className="task-error"><p>The task may have changed. Your input is preserved. Load the latest task and review it before saving again.</p><Button variant="outline" onClick={() => void reload()}>Load latest task</Button></div>}
    <p role="status">{feedback}</p>
    <fieldset disabled={mutation.isPending || conflict} className="task-action-fieldset">
      {(actor === task.owner || managers.includes(actor)) && <Button variant="outline" onClick={() => setEditing(!editing)}>{editing ? 'Cancel editing' : 'Edit task'}</Button>}
      {editing ? <TaskEditor key={`edit-${id}`} actor={actor} initial={task} busy={mutation.isPending} onSave={fields => save('', { ...fields, note: 'Task details updated' })} /> : <TaskActions key={reset} task={task} actor={actor} save={save} />}
    </fieldset>
    <section className="task-history"><h3>History</h3><ErrorNotice error={history.error} />{history.data?.items.map(event => <article key={event.id}><div><strong>{event.actor_label || actorNames[event.actor] || event.actor}</strong> · {event.kind.replaceAll('_', ' ')}</div><small>{dateTime(event.at)} · v{event.version}</small>{event.note && <p>{event.note}</p>}{Object.keys(event.changes).length > 0 && <details><summary>{Object.keys(event.changes).join(', ').replaceAll('_', ' ')}</summary>{Object.entries(event.changes).map(([field, change]) => <div className="task-change" key={field}><strong>{field.replaceAll('_', ' ')}</strong><div>Before: {JSON.stringify(change.before)}</div><div>After: {JSON.stringify(change.after)}</div></div>)}</details>}</article>)}{history.data && history.data.total > 30 && <div className="task-buttons"><Button variant="outline" disabled={!historyOffset} onClick={() => setHistoryOffset(Math.max(0, historyOffset - 30))}>Newer</Button><Button variant="outline" disabled={historyOffset + 30 >= history.data.total} onClick={() => setHistoryOffset(historyOffset + 30)}>Older</Button></div>}</section>
  </>}</aside>;
}

function TaskActions({ task, actor, save }: { task: TeamTask; actor: string; save: (endpoint: string, body: Record<string, unknown>) => void }) {
  const [action, setAction] = useState('note');
  const [note, setNote] = useState('');
  const [proof, setProof] = useState('');
  const [question, setQuestion] = useState('');
  const [options, setOptions] = useState('');
  const owner = actor === task.owner, manager = managers.includes(actor);
  const consequential = task.risks.length > 0 && task.approval_status !== 'approved';
  function submit(event: FormEvent) {
    event.preventDefault();
    if (action === 'note') save('/notes', { note });
    else if (action === 'decision') save('/decision', { answer: note });
    else if (action === 'approve' || action === 'decline') save('/decision', { approved: action === 'approve', note });
    else save('/status', { status: action, note, proof, question, options: options.split('\n').map(value => value.trim()).filter(Boolean) });
  }
  return <form onSubmit={submit} className="task-action-form"><h3>Update task</h3>
    {manager && <div className="task-buttons">
      <Button type="button" variant="outline" onClick={() => setAction('approve')}>Approve</Button>
      <Button type="button" variant="outline" onClick={() => setAction('decline')}>Decline</Button>
      <Button type="button" variant="outline" disabled={task.status === 'done'} onClick={() => setAction('done')}>Mark done</Button>
    </div>}
    <label>Action<select value={action} onChange={e => setAction(e.target.value)}>
      <option value="note">Add a progress note</option>
      {(owner || manager) && statuses.filter(status => status !== task.status && (status !== 'dropped' || manager)).map(status => <option key={status} value={status}>{status === 'done' ? 'Mark done' : statusNames[status]}</option>)}
      {manager && <><option value="approve">Approve</option><option value="decline">Decline</option>{!task.risks.length && <option value="decision">Answer the question</option>}</>}
    </select></label>
    {action === 'decision' && <div className="task-buttons">{task.options.map((option, index) => <Button key={index} type="button" variant="outline" onClick={() => setNote(option)}>{option}</Button>)}</div>}
    <label>{action === 'decision' ? 'Your answer' : action === 'approve' && actor === 'chief_of_staff' ? 'Where Salar gave the OK' : 'Note'}<textarea value={note} onChange={e => setNote(e.target.value)} required={['note', 'decision', 'dropped'].includes(action) || (action === 'approve' && actor === 'chief_of_staff')} maxLength={action === 'approve' || action === 'decline' ? 2000 : 10000} /></label>
    {action === 'done' && <label>Completion proof<textarea required value={proof} onChange={e => setProof(e.target.value)} placeholder="A URL, PR number, check result, or other evidence" maxLength={10000} /></label>}
    {action === 'waiting_on_salar' && !consequential && <><label>Question<input value={question} onChange={e => setQuestion(e.target.value)} maxLength={2000} /></label><label>Options — one per line<textarea value={options} onChange={e => setOptions(e.target.value)} placeholder={'Option one\nOption two'} /></label></>}
    <Button>Save update</Button>
  </form>;
}

function TaskEditor({ actor, initial, busy, onSave }: { actor: string; initial?: TeamTask; busy: boolean; onSave: (fields: TaskFields) => void }) {
  const [fields, setFields] = useState<TaskFields>(() => initial ? { ...initial } : emptyTask(actor));
  const [dependencies, setDependencies] = useState(initial?.blocked_by.join(', ') || '');
  const canEdit = !initial || managers.includes(actor) || actor === initial.owner;
  const set = <K extends keyof TaskFields>(key: K, value: TaskFields[K]) => setFields(previous => ({ ...previous, [key]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    const keys: (keyof TaskFields)[] = ['title', 'outcome', 'owner', 'priority', 'due_on', 'check_by', 'blocked_by', 'blocked_reason', 'links', 'measurement', 'risks', 'authorization'];
    const body = Object.fromEntries(keys.map(key => [key, key === 'blocked_by' ? dependencies.split(',').map(value => value.trim()).filter(Boolean).map(Number) : fields[key]]));
    onSave(body as TaskFields);
  }
  const measurement = fields.measurement || { metric: '', baseline: '', recheck_on: null, result: '' };
  return <form className="task-editor" onSubmit={submit}>
    <label>Title<input required value={fields.title} disabled={!canEdit} maxLength={250} onChange={e => set('title', e.target.value)} /></label>
    <label>What does done look like?<textarea required value={fields.outcome} disabled={!canEdit} maxLength={1000} onChange={e => set('outcome', e.target.value)} /></label>
    <div className="task-form-grid"><label>Owner<select value={fields.owner} disabled={!canEdit} onChange={e => set('owner', e.target.value)}>{Object.entries(actorNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><label>Priority<select disabled={!canEdit} value={fields.priority} onChange={e => set('priority', e.target.value)}>{['P0', 'P1', 'P2', 'P3'].map(priority => <option key={priority}>{priority}</option>)}</select></label><label>Due date<input disabled={!canEdit} type="date" value={fields.due_on || ''} onChange={e => set('due_on', e.target.value || null)} /></label><label>Check by<input disabled={!canEdit} type="date" value={fields.check_by || ''} onChange={e => set('check_by', e.target.value || null)} /></label></div>
    <details><summary>Dependencies and links</summary><fieldset disabled={!canEdit} className="task-action-fieldset"><label>Blocked by task IDs<input value={dependencies} pattern="\s*([1-9][0-9]*\s*(,\s*[1-9][0-9]*\s*)*)?" placeholder="12, 34" onChange={e => setDependencies(e.target.value)} /></label><label>Blocked reason<input maxLength={2000} value={fields.blocked_reason} onChange={e => set('blocked_reason', e.target.value)} /></label>
      {fields.links.map((link, index) => { const update = (key: keyof TaskLink, value: string) => set('links', fields.links.map((item, i) => i === index ? { ...item, [key]: value } : item)); return <div key={index} className="task-link-editor"><label>Link type<select value={link.kind} onChange={e => update('kind', e.target.value)}>{['product', 'collection', 'page', 'blog_article', 'opportunity_task', 'task', 'pr', 'file', 'url'].map(kind => <option key={kind} value={kind}>{kind.replaceAll('_', ' ')}</option>)}</select></label><label>Label<input value={link.label} onChange={e => update('label', e.target.value)} /></label><label>ID or file path<input value={link.id} onChange={e => update('id', e.target.value)} /></label><label>Shopify handle<input value={link.handle} onChange={e => update('handle', e.target.value)} /></label>{link.kind === 'blog_article' && <label>Blog handle<input value={link.blog_handle} onChange={e => update('blog_handle', e.target.value)} /></label>}<label>URL<input type="url" value={link.url} onChange={e => update('url', e.target.value)} /></label><Button type="button" variant="ghost" onClick={() => set('links', fields.links.filter((_, i) => i !== index))}>Remove link</Button></div>; })}<Button type="button" variant="outline" onClick={() => set('links', [...fields.links, blankLink()])}>Add link</Button></fieldset>
    </details>
    <details><summary>Measurement</summary><fieldset disabled={!canEdit} className="task-action-fieldset"><label>Metric<input value={measurement.metric} onChange={e => set('measurement', { ...measurement, metric: e.target.value })} /></label><label>Baseline<textarea value={measurement.baseline} onChange={e => set('measurement', { ...measurement, baseline: e.target.value })} /></label><label>Recheck date<input type="date" value={measurement.recheck_on || ''} onChange={e => set('measurement', { ...measurement, recheck_on: e.target.value || null })} /></label><label>Result<textarea value={measurement.result} onChange={e => set('measurement', { ...measurement, result: e.target.value })} /></label>{fields.measurement && <Button type="button" variant="ghost" onClick={() => set('measurement', null)}>Remove measurement</Button>}</fieldset></details>
    <details><summary>Risk approval</summary><p>Tasks with risks need a manager’s approval before work starts or finishes. Changing the risk list requests a new approval.</p>{Object.entries(riskNames).map(([id, name]) => <label className="task-checkbox" key={id}><input type="checkbox" disabled={!canEdit} checked={fields.risks.includes(id)} onChange={e => set('risks', e.target.checked ? [...fields.risks, id] : fields.risks.filter(risk => risk !== id))} />{name}</label>)}<label>Existing authorization<textarea disabled={!canEdit} maxLength={2000} value={fields.authorization} onChange={e => set('authorization', e.target.value)} /></label></details>
    <Button disabled={busy}>{busy ? 'Saving…' : initial ? 'Save task details' : 'Create task'}</Button>
  </form>;
}
