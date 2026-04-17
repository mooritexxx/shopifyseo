import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.app.routers.actions import router as actions_router
from backend.app.routers.article_ideas import router as article_ideas_router
from backend.app.routers.ai_stream import router as ai_stream_router
from backend.app.routers.auth import router as auth_router
from backend.app.routers.blogs import router as blogs_router
from backend.app.routers.content import router as content_router
from backend.app.routers.dashboard import router as dashboard_router
from backend.app.routers.operations import router as operations_router
from backend.app.routers.sidekick import router as sidekick_router
from backend.app.routers.products import router as products_router
from backend.app.routers.keywords import router as keywords_router
from backend.app.routers.clusters import router as clusters_router
from backend.app.routers.embeddings import router as embeddings_router
from backend.app.routers.image_seo import router as image_seo_router
from backend.app.routers.google_ads_lab import router as google_ads_lab_router
from backend.app.routers.status import router as status_router


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Recover denormalized PageSpeed columns from SQLite cache after crashes or interrupted syncs."""
    log = logging.getLogger(__name__)
    try:
        from backend.app.db import open_db_connection
        from shopifyseo.dashboard_store import refresh_pagespeed_columns_from_cache_for_all_cached_objects

        conn = open_db_connection()
        try:
            n = refresh_pagespeed_columns_from_cache_for_all_cached_objects(conn)
            log.info("PageSpeed catalog reconciled from cache (%s object(s))", n)
        finally:
            conn.close()
    except Exception:
        log.warning("Startup PageSpeed reconcile failed", exc_info=True)
    yield


app = FastAPI(title="Shopify Agentic SEO API", version="0.1.0", lifespan=lifespan)
# SPA is served from the same origin (port 8000); no CORS needed for local use.

app.include_router(article_ideas_router)
app.include_router(dashboard_router)
app.include_router(products_router)
app.include_router(content_router)
app.include_router(blogs_router)
app.include_router(keywords_router)
app.include_router(clusters_router)
app.include_router(operations_router)
app.include_router(status_router)
app.include_router(sidekick_router)
app.include_router(actions_router)
app.include_router(ai_stream_router)
app.include_router(auth_router)
app.include_router(embeddings_router)
app.include_router(image_seo_router)
app.include_router(google_ads_lab_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": {"code": f"http_{exc.status_code}", "message": detail}})


@app.exception_handler(sqlite3.DatabaseError)
async def sqlite_database_error_handler(_, exc: sqlite3.DatabaseError):
    """SQLite corruption / unreadable DB — return JSON instead of a generic 500 HTML/empty body."""
    msg = str(exc).strip() or "SQLite database error"
    hint = ""
    if "malformed" in msg.lower():
        hint = (
            " Restore `shopify_catalog.sqlite3` from a backup, or recover: "
            '`sqlite3 shopify_catalog.sqlite3 ".recover" | sqlite3 shopify_catalog_recovered.sqlite3` '
            "then replace the original after backing it up, or delete the file for a fresh catalog (you will re-sync)."
        )
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "error": {"code": "sqlite_database_error", "message": msg + hint},
        },
    )


if (FRONTEND_DIST / "assets").exists():
    app.mount("/app/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="spa-assets")


@app.get("/", include_in_schema=False)
def root_redirect():
    """SPA is served under /app — avoid a bare 404 when users open the server root."""
    return RedirectResponse(url="/app/", status_code=307)


@app.get("/app/{path:path}")
def app_shell(path: str = ""):
    index_html = FRONTEND_DIST / "index.html"
    if not index_html.exists():
        raise HTTPException(status_code=503, detail="Frontend bundle is not built yet. Run the Vite build from /frontend.")
    return FileResponse(
        index_html,
        headers={
            # Keep the SPA shell fresh so removed fields/routes do not linger in a cached bundle.
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
