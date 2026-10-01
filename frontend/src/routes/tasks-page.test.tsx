import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { TasksPage } from './tasks-page';
import type { TeamTask } from '../lib/team-tasks';

const base: TeamTask = { id: 1, version: 1, title: 'Choose a product', outcome: 'Product selected', owner: 'jimmy', requester: 'chief_of_staff', priority: 'P1', status: 'waiting_on_salar', due_on: null, check_by: null, blocked_by: [], blocked_reason: '', links: [], measurement: null, risks: [], authorization: '', requires_review: false, proof: '', question: 'Which product?', options: ['A', 'B'], latest_decision: null, approval_status: 'not_required', created_at: '2026-09-30T12:00:00Z', last_log_at: '2026-09-30T12:00:00Z', completed_at: null };
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.forEach(client => client.clear()); clients.length = 0; sessionStorage.clear(); vi.unstubAllGlobals(); });
function mount(path = '/tasks?task=1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(client);
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><TasksPage /></MemoryRouter></QueryClientProvider>);
}
function mockApi(actor = 'salar', task = base, conflict = false) {
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST' || options.method === 'PATCH') {
      if (conflict) return { ok: false, status: 409, json: async () => ({ error: { message: 'Task changed. Reload it and retry with the current version.' } }) };
      return { ok: true, json: async () => ({ data: { ...task, version: 2, status: 'todo', latest_decision: { actor, at: task.created_at, question: task.question, answer: 'A', approved: null } } }) };
    }
    const data = url.endsWith('/actors') ? { current: actor } : url.includes('/events') ? { items: [], total: 0 } : url === '/api/tasks/1' ? task : { items: [task], total: 1 };
    return { ok: true, json: async () => ({ data }) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
it('requires a personal token and never asks the browser to choose an actor', async () => {
  const fetcher = mockApi(); mount('/tasks');
  expect(screen.getByRole('heading', { name: 'Connect your identity' })).toBeVisible();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Personal token'), { target: { value: 'my-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  await screen.findByRole('button', { name: 'New task' });
  expect(sessionStorage.getItem('task-token')).toBe('my-token');
  expect(fetcher.mock.calls.every(([, options]) => (options.headers as Record<string, string>)['X-Task-Token'] === 'my-token')).toBe(true);
});
it('sends a versioned decision and displays the saved answer', async () => {
  sessionStorage.setItem('task-token', 'secret'); const fetcher = mockApi(); mount();
  fireEvent.change(await screen.findByLabelText('Action'), { target: { value: 'decision' } });
  fireEvent.click(screen.getByRole('button', { name: /^A$/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save update' }));
  await screen.findByText('Salar’s latest decision');
  const call = fetcher.mock.calls.find(([url]) => url.endsWith('/decision'));
  expect(JSON.parse(call![1].body as string)).toEqual({ version: 1, answer: 'A', approved: null });
});
it('keeps a progress note on conflict and requires an explicit reload', async () => {
  sessionStorage.setItem('task-token', 'secret'); mockApi('jimmy', { ...base, status: 'in_progress', question: '', options: [] }, true); mount();
  fireEvent.change(await screen.findByLabelText('Note'), { target: { value: 'Preserve this evidence' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save update' }));
  await screen.findByRole('button', { name: 'Load latest task' });
  expect(screen.getByLabelText('Note')).toHaveValue('Preserve this evidence');
  expect(screen.getByRole('button', { name: 'Save update' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Load latest task' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save update' })).not.toBeDisabled());
  expect(screen.getByLabelText('Note')).toHaveValue('Preserve this evidence');
});
it('does not offer Chief of Staff approval for their own work', async () => {
  sessionStorage.setItem('task-token', 'secret'); mockApi('chief_of_staff', { ...base, owner: 'chief_of_staff', status: 'review', requires_review: true }); mount();
  await screen.findByLabelText('Action');
  expect(screen.queryByRole('option', { name: 'Approve completion' })).not.toBeInTheDocument();
});
it('requires an explicit approve or decline choice for consequential decisions', async () => {
  sessionStorage.setItem('task-token', 'secret'); mockApi('salar', { ...base, risks: ['live_prices'], approval_status: 'pending', requires_review: true }); mount();
  fireEvent.change(await screen.findByLabelText('Action'), { target: { value: 'decision' } });
  expect(screen.getByLabelText('Approve this action?')).toBeRequired();
});
