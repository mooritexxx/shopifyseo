# Agent / dev notes

## Live testing — **do this after code changes** (project default)

The maintainer tests only on **`http://127.0.0.1:8000/app/`** (FastAPI + built SPA from `frontend/dist`). No Vite dev server.

**Agents: this is mandatory.** When you change code, **you** run the commands in the terminal before finishing — do not ask the maintainer to restart or rebuild unless they have no agent shell.

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
