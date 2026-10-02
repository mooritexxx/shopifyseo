export const actorNames: Record<string, string> = {
  salar: 'Salar', chief_of_staff: 'Chief of Staff', jimmy: 'Jimmy (SEO)', merchandiser: 'Merchandiser',
  blogger: 'Blogger', social: 'Social', price_analyst: 'Price Analyst', code_improver: 'Code Improver'
};
export const statuses = ['todo', 'in_progress', 'blocked', 'waiting_on_salar', 'done', 'dropped'] as const;
export type TaskStatus = typeof statuses[number];
export const statusNames: Record<TaskStatus, string> = { todo: 'To do', in_progress: 'In progress', blocked: 'Blocked', waiting_on_salar: 'Needs Salar', done: 'Done', dropped: 'Dropped' };
export const riskNames: Record<string, string> = { spending: 'Spends money', external_send: 'Sends outside the business', deletion: 'Deletes things', live_prices: 'Changes live prices' };
export type TaskLink = { kind: string; label: string; id: string; handle: string; blog_handle: string; url: string };
export type Measurement = { metric: string; baseline: string; recheck_on: string | null; result: string };
export type TaskFields = {
  title: string; outcome: string; owner: string; priority: string; due_on: string | null; check_by: string | null;
  blocked_by: number[]; blocked_reason: string; links: TaskLink[]; measurement: Measurement | null;
  risks: string[]; authorization: string;
};
export type TeamTask = TaskFields & {
  id: number; version: number; status: TaskStatus; requester: string; created_at: string; last_log_at: string;
  completed_at: string | null; proof: string; question: string; options: string[]; approval_status: string; approval_by?: string | null; approval_at?: string | null;
  latest_decision: { actor: string; actor_label?: string; note?: string; at: string; answer: string; question: string; approved: boolean | null } | null;
};
export type TaskEvent = { id: number; task_id: number; actor: string; actor_label?: string; kind: string; at: string; version: number; note: string; changes: Record<string, { before: unknown; after: unknown }> };
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export class TaskApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export async function taskRequest<T>(path = '', method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/web/tasks${path}`, { method, headers: { 'X-Task-Web': '1', ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json();
  if (!response.ok) {
    const validation = Array.isArray(result.detail) ? result.detail.map((item: { loc: string[]; msg: string }) => `${item.loc.slice(1).join('.')}: ${item.msg}`).join('; ') : '';
    throw new TaskApiError(result.error?.message || validation || 'Unable to save task', response.status);
  }
  return result.data as T;
}
export function taskLinkHref(link: TaskLink): string | null {
  if (link.url && /^https?:\/\//.test(link.url)) return link.url;
  const handle = encodeURIComponent(link.handle);
  if (link.kind === 'task') return `/app/tasks?task=${encodeURIComponent(link.id)}`;
  if (link.kind === 'opportunity_task') return `/app/opportunities`;
  if (handle && ['product', 'collection', 'page'].includes(link.kind)) {
    const segment = { product: 'products', collection: 'collections', page: 'pages' }[link.kind];
    return `/app/${segment}/${handle}`;
  }
  if (handle && link.kind === 'blog_article' && link.blog_handle) return `/app/articles/${encodeURIComponent(link.blog_handle)}/${handle}`;
  return null;
}
