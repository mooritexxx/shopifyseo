# Contributing to ShopifySEO

Thanks for your interest in contributing. This guide covers development setup, conventions, and the pull-request workflow.

## Prerequisites

- **Python 3.10+** (3.12 recommended)
- **Node.js 18+** (20 recommended) with npm
- A Shopify store and custom app credentials for catalog sync (client credentials flow)
- Optional: Google OAuth credentials (Search Console / GA4), DataForSEO credentials, and at least one LLM API key

## Development setup

```bash
git clone https://github.com/mooritexxx/shopifyseo.git
cd shopifyseo

python3 -m venv .venv && source .venv/activate
pip install -e ".[dev]"
pip install -r backend/requirements.txt

cd frontend && npm ci && cd ..

cp .env.example .env
# Edit .env if you use environment-based config; most settings can also be set in the app Settings UI.
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the backend, `shopifyseo` package, and frontend fit together.

## Running locally

The FastAPI app serves the **production-built** SPA from `frontend/dist` on port **8000** (this is what maintainers use for manual testing).

```bash
make build-frontend
make run-backend
```

Open **http://127.0.0.1:8000/app/** (hard-refresh with Cmd+Shift+R after frontend rebuilds).

Convenience: `./start_app.sh` builds the frontend and starts the backend.

For day-to-day UI work you can run `cd frontend && npm run dev` **and** the backend in two terminals, but always verify with a production build before merging, because that is what ships.

## Code style

| Area | Tool | Config |
|------|------|--------|
| Python formatting and linting | [ruff](https://docs.astral.sh/ruff/) | `pyproject.toml` |
| Python types | [mypy](https://mypy-lang.org/) | `pyproject.toml` |
| TypeScript | `tsc --noEmit` | `tsconfig.app.json` |

```bash
ruff check . --fix
ruff format .
cd frontend && npx tsc --noEmit
```

Optional: [pre-commit](https://pre-commit.com/) using `.pre-commit-config.yaml`.

## Testing

```bash
make test-api
# or
PYTHONPATH=. python -m pytest tests/test_api.py -q

cd frontend && npm test
```

CI runs a **minimal** API smoke test and frontend typecheck (see `.github/workflows/ci.yml`). The full `tests/` tree may include additional modules; run targeted files while larger suites are being stabilized.

## Branch naming

- `feat/short-description` — new features
- `fix/short-description` — bug fixes
- `refactor/short-description` — internal improvements
- `docs/short-description` — documentation only

## Pull request process

1. Branch from `main`.
2. Keep changes focused; avoid unrelated refactors in the same PR.
3. Run tests and linters that apply to your change.
4. Describe **what** changed and **why** in the PR body (the template prompts for this).
5. Link related issues when applicable.

## Community

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — expected behavior in issues and PRs.
- [SECURITY.md](SECURITY.md) — how to report vulnerabilities privately.

## Architecture notes

- **`shopifyseo/`** — core Python package (AI engine, Shopify sync, Google integrations, research).
- **`backend/`** — FastAPI routers, schemas, and service layer.
- **`frontend/`** — React + TypeScript SPA (Vite, Tailwind, Radix UI).
- **SQLite** — local database for catalog, settings, and caches.

See also `TECHNICAL_DOC.md` when present for deeper detail.
