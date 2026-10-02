import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { TasksPage } from './tasks-page';
import type { TeamTask } from '../lib/team-tasks';

const base: TeamTask = { id: 1, version: 1, title: 'Choose a product', outcome: 'Product selected', owner: 'jimmy', requester: 'chief_of_staff', priority: 'P1', status: 'waiting_on_salar', due_on: null, check_by: null, blocked_by: [], blocked_reason: '', links: [], measurement: null, risks: [], authorization: '', proof: '', question: 'Which product?', options: ['A', 'B'], latest_decision: null, approval_status: 'not_required', created_at: '2026-09-30T12:00:00Z', last_log_at: '2026-09-30T12:00:00Z', completed_at: null };
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
    const data = url.endsWith('/actors') ? { current: actor } : url.includes('/events') ? { items: [], total: 0 } : url === '/api/web/tasks/1' ? task : { items: [task], total: 1 };
    return { ok: true, json: async () => ({ data }) };
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
it('opens as Salar without a token and clears the old saved credential', async () => {
  sessionStorage.setItem('task-token', 'old-agent-token');
  const fetcher = mockApi(); mount('/tasks');
  await screen.findByRole('button', { name: 'New task' });
  expect(screen.queryByLabelText('Personal token')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();
  expect(sessionStorage.getItem('task-token')).toBeNull();
  expect(fetcher.mock.calls.every(([url, options]) => url.startsWith('/api/web/tasks') && !('X-Task-Token' in (options.headers as Record<string, string>)))).toBe(true);
});
it('opens compact rows in a detail dialog and closes back to the list', async () => {
  mockApi(); mount('/tasks');
  expect(await screen.findByRole('table')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: 'Open task #1: Choose a product' }));
  expect(await screen.findByRole('dialog', { name: 'Task #1' })).toBeVisible();
  expect(await screen.findByText('Product selected')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});
it('sends a versioned decision and displays the saved answer', async () => {
  sessionStorage.setItem('task-token', 'secret'); const fetcher = mockApi(); mount();
  fireEvent.change(await screen.findByLabelText('Action'), { target: { value: 'decision' } });
  fireEvent.click(screen.getByRole('button', { name: /^A$/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save update' }));
  await screen.findByText('Latest manager decision');
  const call = fetcher.mock.calls.find(([url]) => url.endsWith('/decision'));
  expect(JSON.parse(call![1].body as string)).toEqual({ version: 1, answer: 'A' });
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
it('shows manager actions for another owner and requires completion proof', async () => {
  const fetcher = mockApi('salar', { ...base, owner: 'merchandiser', status: 'in_progress' }); mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Mark done' }));
  expect(screen.getByLabelText('Completion proof')).toBeRequired();
  expect(screen.getByRole('button', { name: 'Approve' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Decline' })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'Review' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Completion proof'), { target: { value: 'Checks passed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save update' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.endsWith('/status'))).toBe(true));
  const call = fetcher.mock.calls.find(([url]) => url.endsWith('/status'));
  expect(JSON.parse(call![1].body as string)).toMatchObject({version:1,status:'done',proof:'Checks passed'});
});
it('sends explicit approval from the manager action button', async () => {
  const fetcher = mockApi('salar', { ...base, risks: ['live_prices'], approval_status: 'pending' }); mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save update' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.endsWith('/decision'))).toBe(true));
  const call = fetcher.mock.calls.find(([url]) => url.endsWith('/decision'));
  expect(JSON.parse(call![1].body as string)).toEqual({ version: 1, approved: true, note: '' });
});
it('lets managers edit progress on completed tasks belonging to another owner', async () => {
  mockApi('salar', {...base, status:'done', proof:'Verified'}); mount();
  fireEvent.click(await screen.findByRole('button', {name:'Edit task'}));
  expect(screen.getByLabelText('Due date')).not.toBeDisabled();
  expect(screen.getByLabelText('Priority')).not.toBeDisabled();
  expect(screen.queryByText('Require review before completion')).not.toBeInTheDocument();
});
