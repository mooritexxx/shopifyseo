# ShopifySEO

ShopifySEO is a professional SEO performance and optimization platform purpose-built for Shopify store operators. It transforms complex search data into actionable insights, allowing solo operators to audit their catalog, monitor search growth, and optimize content using AI.

## Key Features

### SEO Performance Dashboard
A high-level analytics suite designed for clarity and action.
- **Google Search Console (GSC) Integration:** Real-time tracking of clicks, impressions, and CTR with matched-day period comparisons (MTD vs. prior month).
- **GA4 Analytics:** Direct monitoring of site sessions and views to correlate search traffic with user behavior.
- **Indexing Rollup:** Comprehensive overview of Google indexing status across products, collections, and pages.
- **Catalog Completion Tracking:** A "Debt" tracking system that calculates the percentage of the store that is fully SEO-optimized.
- **Custom Goal Tracking:** Environment-configurable daily targets for clicks and sessions with visual reference lines.

### Shopify Catalog Intelligence
Deep integration with the Shopify Admin API to audit and manage store entities.
- **Automated Catalog Sync:** Regular synchronization of products, collections, pages, and blog posts into a local SEO database.
- **SEO Audit Engine:** Identification of "thin content," missing meta titles, and missing meta descriptions.
- **Entity-Level Metrics:** Performance breakdowns for individual products and collections, bridging the gap between Shopify data and GSC performance.

### AI-Powered Optimization (The AI Engine)
A sophisticated AI layer that assists in content creation and strategy.
- **Sidekick:** A contextual AI chat assistant that appears on product and collection detail pages, providing real-time SEO advice and optimization suggestions.
- **Article Idea Generator:** AI-driven brainstorming for blog content based on SEO gaps and keyword opportunities.
- **Content Generation:** Tools to draft and refine SEO-optimized blog posts and product descriptions.
- **Embedding Store:** Vector-based storage for semantic search and AI context retrieval.

### Utility & Workflow Tools
- **SEO Slug Optimization:** Tools for generating and validating search-friendly URLs.
- **CSV Export:** Ability to export full audit data and performance rollups for external reporting.
- **Blog Management:** A pipeline for taking AI-generated ideas from draft to a final SEO-ready state.

---

## Prerequisites

- **Python** 3.10+ (tested with 3.13.7)
- **Node.js** 18+ with npm

---

## Quick Start

### 1. Clone and set up the environment

```bash
git clone https://github.com/<your-org>/shopifyseo.git
cd shopifyseo

# Python virtual environment
python3 -m venv .venv
source .venv/bin/activate    # macOS / Linux
# .venv\Scripts\activate     # Windows

# Install Python dependencies
pip install -r backend/requirements.txt

# Install frontend dependencies
cd frontend && npm ci && cd ..
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in the values you need. At a minimum you will want:

| Variable | Required for |
|----------|-------------|
| `SHOPIFY_SHOP` / `SHOPIFY_STORE_URL` | Catalog sync, preview links |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | Shopify Admin API access |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | GSC and GA4 integration |
| At least one AI key (`OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.) | AI features (Sidekick, generation) |

Most settings can also be configured in the **Settings** page inside the app, where they are stored in the local SQLite database. Environment variables take precedence.

### 3. Build and run

```bash
# Build the frontend SPA
cd frontend && npm run build && cd ..

# Start the backend (serves the SPA from frontend/dist)
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/app/** in your browser.

---

## Integrations Setup

### Shopify

1. In your Shopify admin, go to **Settings > Apps and sales channels > Develop apps**.
2. Create a custom app and grant these Admin API scopes: `read_products`, `write_products`, `read_content`, `write_content`, `read_themes`.
3. Install the app and copy the **Admin API access token**, **Client ID**, and **Client Secret** to your `.env` or the Settings page.

### Google (Search Console + GA4)

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Search Console API** and **Google Analytics Data API**.
3. Under **Credentials**, create an **OAuth 2.0 Client ID** (Web application).
4. Set the authorized redirect URI to: `http://127.0.0.1:8000/auth/google/callback`
5. Copy the Client ID and Client Secret to your `.env` or the Settings page.
6. Connect your account via the **Google Signals** page in the app.

### DataForSEO (keyword and competitor research)

Set `DATAFORSEO_API_LOGIN` and `DATAFORSEO_API_PASSWORD` in your `.env` or Settings (Integrations) to enable seed keyword expansion, competitor discovery, and related Labs endpoints. Optional: `MOZ_API_TOKEN` for Moz manual research on target keywords.

---

## Project Structure

```
shopifyseo/              Core Python package (dashboard, AI engine, Shopify sync)
backend/app/         FastAPI application (routers, schemas, services)
frontend/            React + TypeScript SPA (Tailwind CSS, Recharts)
tests/               Test suite (pytest + Vitest)
docs/                Product specs and blueprints
```

The SQLite database is created automatically at the repo root on first run.

---

## Development

The project uses a proper Python package structure. All imports should use:
- `from shopifyseo.module_name import ...` for core modules
- `from backend.app.module import ...` for backend modules

### Tests

```bash
# Python tests
PYTHONPATH=. python3 -m pytest tests/ -v

# Frontend tests
cd frontend && npm run test
```

### Frontend rebuild

After changes under `frontend/src/`, rebuild the SPA so the backend serves updated assets:

```bash
cd frontend && npm run build
```

Use `npm run rebuild` for a clean build (clears Vite cache).

For a deeper technical reference, see `TECHNICAL_DOC.md`.

---

## License

[MIT](LICENSE)
