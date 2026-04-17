# Contributing to Shopify Agentic SEO

Thanks for your interest in contributing! This guide covers the dev setup, conventions, and PR workflow.

## Prerequisites

- **Python 3.10+** (3.12 recommended)
- **Node.js 18+** (20 recommended) with npm
- A Shopify store with an Admin API access token (for catalog sync)
- Optional: Google OAuth credentials (for Search Console / GA4), DataForSEO credentials (for keyword and competitor research), and at least one LLM API key

## Development Setup

```bash
# Clone the repo
git clone https://github.com/<your-org>/shopify-agentic-seo.git
cd shopify-agentic-seo

# Python deps (virtual env recommended)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Frontend deps
cd frontend && npm ci && cd ..

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

## Running Locally

The FastAPI backend serves the built SPA from `frontend/dist` on port 8000.

```bash
# Build the frontend
cd frontend && npm run build && cd ..

# Start the backend
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000/app/** (hard-refresh with Cmd+Shift+R after rebuilds).

## Code Style

| Area | Tool | Config |
|------|------|--------|
| Python formatting & linting | [ruff](https://docs.astral.sh/ruff/) | `pyproject.toml` |
| Python types | [mypy](https://mypy-lang.org/) | `pyproject.toml` |
| TypeScript types | `tsc --noEmit` | `tsconfig.app.json` |
| Frontend formatting | Prettier (via editor) | defaults |

Run linters before committing:

```bash
ruff check . --fix
ruff format .
cd frontend && npx tsc --noEmit
```

## Testing

```bash
# Python tests
python -m pytest

# Frontend tests
cd frontend && npm test
```

## Branch Naming

Use descriptive branch names with a prefix:

- `feat/short-description` — new features
- `fix/short-description` — bug fixes
- `refactor/short-description` — code improvements
- `docs/short-description` — documentation only

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with clear, focused commits
3. Ensure all tests pass and linters are clean
4. Open a PR with a description of **what** changed and **why**
5. Link any related issues

## Architecture Notes

- **`shopifyseo/`** — core Python package (business logic, AI engine, Shopify sync, Google integrations)
- **`backend/`** — FastAPI routers, schemas, and service layer
- **`frontend/`** — React 19 + TypeScript SPA (Vite, Tailwind, Radix UI)
- **SQLite** — single-file database for catalog, settings, and cache

See `TECHNICAL_DOC.md` for detailed architecture documentation.
