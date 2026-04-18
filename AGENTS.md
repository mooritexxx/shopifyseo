# Agent / dev notes

## Before proposing new features — consult TECHNICAL_DOC.md

[TECHNICAL_DOC.md](TECHNICAL_DOC.md) is the canonical map of what already ships:
routes (§9), screens (§10), services (§5.5), domain-library modules (§5.2–§5.4),
tables (§8), background jobs (§7), settings (§4). Skim the relevant section before
suggesting "we should add X" — it is probably already there.

If you add a router, service, table, or screen, update the matching section in the
**same PR** (see §14 "Keeping this doc in sync" for the trigger table).

## Live testing — **do this after code changes** (project default)

The maintainer tests only on **`http://127.0.0.1:8000/app/`** (FastAPI + built SPA from `frontend/dist`). No Vite dev server.

**Agents: this is mandatory.** After **every** substantive code change, **you** rebuild and restart the local app before handoff — do not ask the maintainer to do it unless there is no agent shell.

**Quick path (from repo root):** `./scripts/dev-restart-local.sh` — stops anything on port **8000**, runs `npm run build` in `frontend/`, then starts uvicorn in the **foreground** (run it in a **background** terminal in the agent, or run the build + uvicorn steps below manually). Use `./scripts/dev-restart-local.sh --rebuild` if the UI still looks cached after `npm run build`.

| What changed | Action |
|--------------|--------|
| Anything under `frontend/` (or Vite/tsc config) | `cd frontend && npm run build` (`npm run rebuild` if cache issues) |
| Anything under `backend/`, `shopifyseo/`, or Python deps | Kill listeners on **8000**, then start: `PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000` (repo root) |
| Both | Build frontend **and** restart backend |

Remind them: hard refresh (**⌘⇧R**) when testing the 8000 bundle. **Do not** start `npm run dev` or port 5173 for this repo.

## Sidekick (in-app detail-page chat)

**Sidekick** is the floating chat on product / collection / page detail routes. When the user says “Sidekick”, they mean this feature.

| Area | Location |
|------|-----------|
| UI + binding hook | `frontend/src/components/sidekick/sidekick-context.tsx` (`SidekickProvider`, `useSidekickBinding`) |
| API | `POST /api/sidekick/chat` |
| Router | `backend/app/routers/sidekick.py` |
| Schemas | `backend/app/schemas/sidekick.py` |
| Service | `dashboard_service.sidekick_chat` → `shopifyseo.sidekick.run_sidekick_turn` |

## After frontend changes (live test on `http://127.0.0.1:8000/app/`)

The FastAPI app serves the SPA from `frontend/dist`. After any change under `frontend/src/` (or frontend config), run a clean production build so the browser picks up assets:

```bash
cd frontend && npm run rebuild
```

Equivalent: `rm -rf dist node_modules/.vite && npm run build`

This is part of the **Live testing** workflow above. Backend: restart if Python changed; `--reload` often suffices if the process was already started with it.

Hard refresh (⌘⇧R) if assets look cached.

## Git — commit and push before handoff

When you change code, **finish the loop**: rebuild the SPA if anything under `frontend/` changed (`cd frontend && npm run build`), restart or rely on `--reload` for Python changes as in the table above, then **commit and push** so the remote matches local:

```bash
git add -A
git commit -m "Short imperative description of the change"
git push
```

Do not end a coding task with only local edits; the maintainer expects the branch pushed.

## Human contributors

For fork/PR workflow, issue etiquette, and optional pre-commit hooks, see [CONTRIBUTING.md](CONTRIBUTING.md).
