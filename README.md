# ShopifySEO

[![CI](https://github.com/mooritexxx/shopifyseo/actions/workflows/ci.yml/badge.svg)](https://github.com/mooritexxx/shopifyseo/actions/workflows/ci.yml)

A self-hosted SEO platform for Shopify store operators. It pulls your catalog, Google Search Console, GA4, PageSpeed, and indexing data into a local SQLite database and provides AI-powered SEO recommendations, keyword clustering, and blog article generation — all running on your own machine with no data leaving your environment.

![ShopifySEO demo](docs/demo2.gif)

Setup takes about 15 minutes.

---

## Features

- **SEO Dashboard** — GSC clicks/impressions, GA4 sessions, PageSpeed scores, and indexing status in one view with period-over-period comparisons
- **Catalog Audit** — Identifies thin content, missing meta titles/descriptions, and incomplete SEO across every product, collection, page, and blog post
- **AI Optimization** — Regenerate SEO titles, meta descriptions, and body copy via OpenRouter (choose models per task in Settings)
- **Sidekick** — Contextual AI chat on every product/collection/page detail view for real-time advice
- **Keyword Clustering** — Groups your GSC queries into intent clusters and maps them to catalog pages
- **Keyword Research** — Seed keyword expansion, competitor discovery, and gap analysis via DataForSEO (optional)
- **Article Ideas & Generation** — AI-driven blog ideation based on SEO gaps, with full draft-to-publish pipeline
- **Image optimization** — Catalog image workflow: vision-assisted alt text, SEO-friendly filenames, optional **WebP** conversion for smaller files, and **normalized dimensions** (square canvas / consistent sizing) before replacing media in Shopify
- **Embeddings** — Background vector indexes for products, pages, keywords, clusters, competitor pages, and more—powers similarity and gap-style analysis (when Gemini embedding API is configured)
- **Google Ads lab** — Experimental keyword workflows against your connected Google Ads account (optional)
- **Everything local** — SQLite database, no SaaS, no usage fees beyond your own API keys

---

## Prerequisites

- **Python** 3.10+
- **Node.js** 18+ with npm

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/mooritexxx/shopifyseo.git
cd shopifyseo
```

### 2. Set up Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

pip install -e .               # installs the shopifyseo package in editable mode
pip install -r backend/requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

| Variable | What it unlocks |
|---|---|
| `SHOPIFY_SHOP` | Your store handle e.g. `your-store.myshopify.com` |
| `SHOPIFY_STORE_URL` | Your public store URL e.g. `https://your-store.com` |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | Shopify catalog sync |
| `GOOGLE_SERVICE_ACCOUNT_FILE` (preferred) or `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | GSC and GA4 data (service account first; OAuth fallback) |
| One AI key (see [AI Providers](#ai-providers)) | All AI features |

Everything else is optional. Most settings can also be configured in the **Settings** page inside the app after first launch.

### 4. Install frontend dependencies

```bash
cd frontend && npm ci && cd ..
```

### 5. Run the app

```bash
./start_app.sh
```

This builds the frontend and starts the backend. Open **http://127.0.0.1:8000/app/** in your browser.

> **Manual alternative:**
> ```bash
> cd frontend && npm run build && cd ..
> PYTHONPATH=. uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
> ```

---

## First Run Workflow

Once the app is open:

1. **Settings → Store** — enter your Shopify shop domain and store URL if not already set via `.env`
2. **Settings → Integrations** — paste your Shopify access token and Google credentials
3. **Google Search & Analytics** — use a service-account key (see below) or open Settings and complete the OAuth fallback to authorize Search Console and GA4
4. **Dashboard → Sync** — run your first sync. It fetches your full Shopify catalog, GSC data, GA4 data, and indexing status. The first run takes a few minutes depending on catalog size
5. Browse the **Products**, **Collections**, **Pages**, and **Blogs** tabs to see your SEO audit results

The database (`shopify_catalog.sqlite3`) is created automatically in the project root on first sync.

---

## Integrations Setup

### Shopify

1. In your Shopify admin go to **Settings → Apps and sales channels → Develop apps**
2. Create a custom app and grant these Admin API scopes: `read_products`, `write_products`, `read_content`, `write_content`, `read_themes`
3. Install the app and copy the **Admin API access token** (starts with `shpat_`) to your `.env` as `SHOPIFY_CLIENT_SECRET`, and the API key as `SHOPIFY_CLIENT_ID`

### Google (Search Console + GA4)

Search Console and GA4 use a **service account first**. OAuth remains as a fallback (`/auth/google/start` and `/callback` are unchanged).

**Service account (preferred)**

1. Create a Google Cloud service account and download its JSON key. Store it outside the repo (mode `600`). Default path: `/home/box/secrets/google-sa.json`. Override with `GOOGLE_SERVICE_ACCOUNT_FILE`.
2. Enable these APIs on that project: **Google Search Console API**, **Google Analytics Data API**, and **Google Analytics Admin API** (the property picker calls `analyticsadmin.googleapis.com/v1beta/accountSummaries`). Enable the **PageSpeed Insights API** for PSI (called with an OAuth `openid` bearer when available, otherwise the public quota; optional `PAGESPEED_API_KEY`).
3. Add the service-account email as a user in Search Console (the property, e.g. `sc-domain:example.com`, with at least Site permissions sufficient to read) and as a Viewer on the GA4 property. A service account only sees properties it was explicitly granted. Do this **before** placing the key file and restarting — if the key is usable, GSC/GA4 calls that return 403 do **not** fall back to OAuth.
4. `pip install -e .` so `cryptography` is in the live venv, then **restart** the app process. A missing `cryptography` package is treated as “service account unavailable” and the app falls back to OAuth; it will not break startup.
5. Verify: `GET /api/google-signals` returns `configured=true`, `connected=true`, `mode="service_account"`. Sync GSC/GA4 from the sidebar.

Service-account-first has **no per-request OAuth fallback**. OAuth is used only when no usable key exists (file missing, unreadable, or `cryptography` not importable) or the token mint itself fails (then a 120s cooldown avoids retrying the token endpoint). To go back to OAuth, remove or rename the key file. If the SA is valid but was not added to the Search Console / GA4 property, those APIs return 403 and stay on the service-account token.

Scopes requested by the service account: `https://www.googleapis.com/auth/webmasters.readonly` and `https://www.googleapis.com/auth/analytics.readonly`. The app does not submit sitemaps or call the Indexing API, so read-only Webmaster is enough.

**OAuth fallback**

1. Under **APIs & Services → Credentials**, create an **OAuth 2.0 Client ID** (type: Web application)
2. Add `http://127.0.0.1:8000/auth/google/callback` as an authorized redirect URI
3. Copy the Client ID and Client Secret to Settings (or `.env`)
4. Click **Connect Google** in Settings → Data sources

**Known limits**

- **Google Ads** stays on the user OAuth token (`adwords` scope). A service account cannot replace it.
- **PageSpeed Insights** does **not** use the service-account token (SA JWTs lack the PSI scope and return 403 `insufficientPermissions`). Order: (1) stored OAuth token with the `openid` scope, obtained via `get_google_access_token` only — if refresh fails, fall through; (2) otherwise call PSI with no bearer (public quota); (3) if `PAGESPEED_API_KEY` is set, append it as the `key` query parameter in both cases. The old “Reconnect Google so the token includes the openid scope” error is gone.
- **GSC per-page breakdowns** (`query` × `country` / `device` / `searchAppearance`) pace at most ~8 calls/s (`GSC_DIMENSIONAL_MIN_INTERVAL_SECONDS`, default 0.125s; env override is read when the pacer is constructed at import / `reset_gsc_dimensional_pacer()`), retry throttle/5xx up to 4 times with 2/4/8s backoff (honoring `Retry-After`), and record real failures as sync `warnings` / `warning_details` (not `errors`). Deliberate `searchAppearance` skips after the breaker go in `warning_details["skipped"]` only. A non-throttle 403 trips a per-run circuit breaker after one warning (`gsc_dimensional_run()`). **`searchAppearance` cannot be combined with other dimensions** ([Search Analytics: getting your performance data](https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data) — “Search appearance is not available as a column along with any other dimensions”); this app’s `["query","searchAppearance"]` + page filter is that unsupported combination. After 5 consecutive 400s with the same reason, remaining `searchAppearance` calls in the run are skipped. No `searchAppearance` rows were ever stored. On any final failure the previous dimension rows are kept.
- **Indexing API** is not used by this app (URL Inspection is Search Console, not `indexing.googleapis.com`).
- Service accounts cannot complete the OAuth consent flow or obtain user-delegated scopes such as `adwords` or `openid`.
- A service account only sees Search Console / GA4 properties it was explicitly granted. There is no per-request OAuth fallback while a key is usable: 403s from a missing property grant stay on the SA token. Remove or rename the key file to use OAuth.

### AI Providers

At least one AI provider is required for SEO generation, Sidekick chat, and article drafting. Pick based on your preference:

| Provider | Best for | Cost |
|---|---|---|
| **Anthropic** (`ANTHROPIC_API_KEY`) | Highest quality SEO copy | Pay per use |
| **OpenAI** (`OPENAI_API_KEY`) | Fast, reliable generation | Pay per use |
| **Google Gemini** (`GEMINI_API_KEY`) | Free tier available | Free / pay per use |
| **Ollama** (`OLLAMA_BASE_URL`) | Fully local, no API cost | Free (runs locally) |
| **OpenRouter** (`OPENROUTER_API_KEY`) | Access many models via one key | Pay per use |

Once configured, select your provider and model in **Settings → AI**.

For image generation (blog article cover images), a separate image model can be configured under `AI_IMAGE_PROVIDER` / `AI_IMAGE_MODEL`.

### DataForSEO (optional)

Enables keyword research, competitor discovery, and SERP analysis. Enter **API login** and **password** on **Settings → Integrations** (or set `DATAFORSEO_API_LOGIN` and `DATAFORSEO_API_PASSWORD` in `.env`). Use **Validate access** to confirm credentials. Without DataForSEO, keyword research is unavailable but everything else works.

---

## Project Structure

```
shopifyseo/        Core Python package — AI engine, Shopify sync, dashboard logic
backend/app/       FastAPI application — routers, schemas, services
frontend/          React + TypeScript SPA (Tailwind CSS, Recharts)
tests/             Test suite (pytest)
skills/            Claude Code skill scripts for bulk SEO operations
docs/              Architecture specs and feature blueprints
start_app.sh       One-command build + run script
.env.example       All available environment variables with documentation
```

---

## Development

### Running tests

```bash
# Python — SQLite unless TEST_DATABASE_URL is a postgres URL (see tests/README.md)
PYTHONPATH=. python3 -m pytest tests/ -v

# Frontend
cd frontend && npm run test
```

### Frontend dev mode

For live-reload during frontend development:

```bash
cd frontend && npm run dev
```

Then run the backend separately:

```bash
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

### Rebuilding the frontend

After changes to `frontend/src/`, rebuild so the backend serves updated assets:

```bash
cd frontend && npm run build
# or: npm run rebuild   (clears Vite cache first)
```

For a deeper technical reference see [TECHNICAL_DOC.md](TECHNICAL_DOC.md).
PostgreSQL cutover tooling (dry-run only; live stays on SQLite) is documented in
[docs/pg-cutover.md](docs/pg-cutover.md).

---

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and PR expectations. High-level layout is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## License

[MIT](LICENSE)
