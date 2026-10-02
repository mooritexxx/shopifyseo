# Team task manager

The shared task board lives at **http://127.0.0.1:8000/app/tasks**. Agents use `/api/tasks` with their individual tokens. The web screen uses `/api/web/tasks` and opens directly as Salar. Existing SEO opportunity tasks remain separate; use `links: [{"kind":"opportunity_task","id":"99"}]` to connect one.

## Identity setup

Run `python3 scripts/provision-task-actors.py` once on the machine hosting the app. It creates missing credentials without replacing existing ones and never prints tokens.

Tokens live outside the repository in `~/.config/shopifyseo/task-actors/` (directory mode 0700, new files 0600). `TASK_MANAGER_TOKEN_DIR` can override that path; set it for both provisioning and the server. Provisioning is explicit, not an HTTP endpoint. Rotate a token by replacing that actor's file with a new cryptographically random token of at least 32 characters. Tokens are read per request, so replacement takes effect immediately. Do not configure duplicate tokens: ambiguous matches fail closed.

| Actor | Fixed ID | Private token file |
|---|---|---|
| Salar | `salar` | `salar.token` |
| Chief of Staff | `chief_of_staff` | `chief_of_staff.token` |
| Jimmy (SEO) | `jimmy` | `jimmy.token` |
| Merchandiser | `merchandiser` | `merchandiser.token` |
| Blogger | `blogger` | `blogger.token` |
| Social | `social` | `social.token` |
| Price Analyst | `price_analyst` | `price_analyst.token` |
| Code Improver | `code_improver` | `code_improver.token` |

Every agent API request sends **`X-Task-Token`**. Identity is resolved by the server; request bodies cannot supply an actor or requester. All eight actors can read all tasks. Agent credentials are never sent to the browser.

### Salar's web access

Open Tasks in the app: there is no task login, token picker or disconnect step. The web routes mirror the agent routes under `/api/web/tasks`, use the same service and data, and always stamp writes as `salar`. The UI removes old task tokens from sessionStorage. Salar's token remains available for deliberate API use but is not needed on the web.

Web access inherits the deployment's trusted-network access (localhost or the existing private Tailscale deployment). Browser requests must include same-origin Fetch Metadata and `X-Task-Web: 1`; an Origin header, when supplied, must match the request Host. These checks reject cross-site browser reads/writes and form submissions. They do not authenticate a person or isolate local agents: a non-browser client on the trusted network can imitate browser headers. Restrict network access to the app accordingly. The agent API continues rejecting missing/invalid tokens even with browser headers.

Tokens distinguish callers on the agent API. Processes sharing a Unix account can still read each other's files; this is not OS-level isolation between agents. Give each routine only its own token path, never copy tokens into task notes, URLs, source control, screenshots, or prompts.

## Permissions and lifecycle

There are two roles: **managers** (`salar`, `chief_of_staff`) and **agents** (every other actor). Identity still comes from the token, never the request body.

- Anyone can create a task for any owner and add notes to any task. The authenticated creator is the requester.
- Managers can edit any task field, change status, reopen, approve, decline, or drop any task, including their own. Agents can edit all task fields and change status only on tasks they own. Agents cannot approve, decline, or drop tasks.
- Statuses are `todo`, `in_progress`, `blocked`, `waiting_on_salar`, `done`, and `dropped`. There is no completion-review step or `requires_review` field. The retired `/review` endpoint is removed.
- A non-empty `risks` list requires manager approval before `in_progress` or `done`. Unapproved transitions return 409 with a clear message. New risky tasks start in `waiting_on_salar`; changing the risk list requests new approval. Ordinary edits and reopening do not revoke approval. Removing all risks removes the approval requirement.
- Managers approve or decline using `/decision` with `approved: true/false`. This sets `approval_status` (`approved` or `declined`), `approval_by`, and `approval_at`, and records the decision in immutable history. Chief of Staff approval requires a short `note` (or existing `answer` field) explaining where Salar gave the OK. Its display attribution is **Chief of Staff (for Salar)**, while the authenticated actor ID stays `chief_of_staff`.
- Managers can record decisions on any task, without first moving it to `waiting_on_salar`. A waiting task returns to `todo`. Declining risky work that is in progress or done returns it to `todo`, clears its completion date, and prevents further execution/completion until approval. Managers can also answer non-risk questions using `answer` without an approval choice.
- Marking done requires a nonblank proof note and all dependencies in `done`, for every role. Dropped dependencies do not count as finished. Editing a done task cannot remove its proof or introduce unfinished dependencies. Proof is recorded evidence; the app does not independently verify it.
- Dropping requires a reason in `note` and a manager. Blocked reasons, questions and options remain useful optional context; they do not introduce additional permission gates.
- Dependencies must exist and cannot form cycles. Version checks apply to every mutation, including notes and decisions. Every successful write increments the version and appends history in the same transaction. On 409, reread and reconcile before retrying. After an uncertain network outcome, read task/history first.

On schema initialization, legacy tasks in `review` become `done` if they contain nonblank proof, otherwise `todo`. The migration removes `requires_review`, retains old history, appends a system migration event and increments each migrated task's version. It is atomic and idempotent; old historical events remain visible as records of the previous workflow. Legacy `denied` decisions become `declined` in current task data.

## Risk classifications

Supported risks remain `spending`, `external_send`, `deletion`, and `live_prices`. The API does not infer risk from free text. `authorization` records an existing instruction but does not bypass the approval gate while risks are present. The task API does not gate Shopify or other external tools, and this change does not edit agents' external routines.

## API contracts

FastAPI's `/docs` and `/openapi.json` expose request schemas. Responses are `{ "ok": true, "data": ... }`; validation errors are HTTP 422. Missing/invalid credentials return 401, denied actions 403, missing tasks 404, and stale versions or incompatible workflow states 409.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/tasks/actors` | Fixed actor names and current caller; no credentials |
| GET | `/api/tasks` | Filters: `owner`, `status`, `stale`, `done_this_week`; `limit` 1–500 (default 100), `offset` |
| POST | `/api/tasks` | Create; returns 201 and complete task |
| GET | `/api/tasks/{id}` | Current task and version |
| PATCH | `/api/tasks/{id}` | `version`, fields to change, optional `note` |
| POST | `/api/tasks/{id}/status` | `version`, `status`, optional `note`, `proof`, `question`, `options` |
| POST | `/api/tasks/{id}/notes` | `version`, nonblank `note` |
| POST | `/api/tasks/{id}/decision` | Manager decision: `version`, `approved` boolean and optional `note`; CoS approval requires an attribution note; `answer` supports non-risk questions |
| GET | `/api/tasks/{id}/events` | Paginated immutable history, newest first |
| GET | `/api/tasks/events` | Global history for rollups; optional ISO `since` with explicit timezone, pagination |

List/history responses contain `items`, `total`, `limit`, `offset`. Sort order for tasks is priority then newest ID. History entries contain event ID, task ID, version, actor, actor_label, kind, UTC timestamp, note, and before/after field changes. There are no history edit/delete endpoints; SQLite triggers also reject log updates/deletes.

Statuses: `todo`, `in_progress`, `blocked`, `waiting_on_salar`, `done`, `dropped`.

Create fields:

```json
{
  "title": "Check opportunity results",
  "outcome": "Record the change in clicks and a next-step recommendation",
  "owner": "jimmy",
  "priority": "P2",
  "due_on": null,
  "check_by": "2026-10-14",
  "blocked_by": [],
  "blocked_reason": "",
  "links": [{"kind": "product", "handle": "example-product"}],
  "measurement": {
    "metric": "Organic clicks over 14 days",
    "baseline": "42 clicks",
    "recheck_on": "2026-10-14",
    "result": ""
  },
  "risks": [],
  "authorization": "Salar's approved SEO improvement work"
}
```

Dates are `YYYY-MM-DD`; absent dates and measurement can be null. Reference kinds: `product`, `collection`, `page`, `blog_article`, `opportunity_task`, `task`, `pr`, `file`, `url`. Each needs an `id`, `handle`, or `url`; task/opportunity references need an existing positive numeric ID encoded as a string. Article deep links also need `blog_handle`. Use `id` for file paths; URLs accept only HTTP(S). ID-only catalog references remain stored even without a resolvable handle. The task screen links handles to existing catalog routes without reading whole-catalog payloads.

## Agent routine example

Read only the token assigned to the current agent. This example keeps credentials out of command arguments and stdout:

```python
import json
from pathlib import Path
from urllib.request import Request, urlopen

token = Path('~/.config/shopifyseo/task-actors/jimmy.token').expanduser().read_text().strip()

def api(path='', method='GET', body=None):
    request = Request(
        'http://127.0.0.1:8000/api/tasks' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'X-Task-Token': token, 'Content-Type': 'application/json'},
        method=method,
    )
    with urlopen(request) as response:
        return json.load(response)['data']

tasks = api('?owner=jimmy&status=todo')
# Read the latest version immediately before any update.
# task = api('/123')
# api('/123/notes', 'POST', {'version': task['version'], 'note': 'Baseline checked: 42 clicks.'})
```

Each routine should read its queue, inspect decisions/dependencies, post substantive progress notes, and attach proof on completion. A mismatched assignment should be marked `blocked` with a reason so Chief of Staff can reassign it. On `409`, re-read and reconcile rather than blindly increasing the version.

## Views and follow-ups

All views use compact task rows with title, priority, owner, status and due/check date. Click a row (or activate its title with the keyboard) to open the task details and history in a dialog. Managers see Approve, Decline and Mark done actions on every task; Mark done requires proof. Escape or Close returns to the list.

- **Needs you:** all `waiting_on_salar` tasks with question/options and manager decision actions.
- **By owner:** compact rows grouped under owner headings, with optional owner/status filters. Counts are explicitly page-scoped when paginated.
- **Stale:** `in_progress` tasks with no log event in at least 48 hours. Notes by any actor count, as do automatic field/status-change events. Reads do not reset this clock.
- **Done this week:** currently completed tasks with completion timestamps since Monday midnight in `America/Vancouver`.
- **All tasks:** searchable by owner/status filters with pagination.

Measurement results and due/check dates are stored, not executed by a scheduler. Agents perform nightly/monthly checks and Friday rollups through the API. There are no notifications, subtasks, tags, comment threads, or automatic external actions in V1.
