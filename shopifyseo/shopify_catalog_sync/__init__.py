#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.getenv(
        "SHOPIFY_CATALOG_DB_PATH",
        Path(__file__).resolve().parent.parent.parent / "shopify_catalog.sqlite3",
    )
)

from .db import ensure_schema, open_db
from .products import sync_product, sync_products, upsert_product
from .collections import sync_collection, sync_collections, upsert_collection
from .pages import sync_page, sync_pages, upsert_page
from .blogs import (
    sync_article,
    sync_blog,
    sync_blogs,
    upsert_blog_article,
    upsert_blog_article_from_admin_create,
    upsert_blog,
)
from .db import fetch_all_blogs, fetch_all_articles_for_blog

_log = logging.getLogger(__name__)


def _bg_embed(db_path: Path, types: tuple[str, ...]) -> None:
    """Run embedding sync in a background thread with its own DB connection."""
    try:
        from shopifyseo.embedding_store import sync_embeddings
        conn = open_db(db_path)
        try:
            for t in types:
                sync_embeddings(conn, object_type=t)
        finally:
            conn.close()
    except Exception:
        _log.warning("Background embedding sync failed", exc_info=True)


def sync_all(db_path: Path, page_size: int) -> dict:
    conn = open_db(db_path)
    try:
        result = {
            "products": sync_products(db_path, page_size),
            "collections": sync_collections(db_path, page_size),
            "pages": sync_pages(db_path, page_size),
            "blogs": sync_blogs(db_path, page_size),
        }
        threading.Thread(
            target=_bg_embed,
            args=(db_path, ("product", "collection", "page", "blog_article", "gsc_queries")),
            daemon=True,
        ).start()
        return result
    finally:
        conn.close()


def probe_shopify_blogs(page_size: int) -> dict:
    """Read-only: list blogs and article counts from Shopify (no DB writes)."""
    blogs = fetch_all_blogs(page_size)
    rows = []
    for blog in blogs:
        articles = fetch_all_articles_for_blog(blog["id"], min(page_size, 250))
        rows.append(
            {
                "id": blog.get("id"),
                "handle": blog.get("handle"),
                "title": blog.get("title"),
                "article_count": len(articles),
            }
        )
    return {
        "shop": (os.getenv("SHOPIFY_SHOP") or "").strip(),
        "blog_count": len(blogs),
        "blogs": rows,
    }


def print_summary(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    counts = {
        "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "variants": conn.execute("SELECT COUNT(*) FROM product_variants").fetchone()[0],
        "images": conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0],
        "metafields": conn.execute("SELECT COUNT(*) FROM product_metafields").fetchone()[0],
        "collections": conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0],
        "collection_metafields": conn.execute("SELECT COUNT(*) FROM collection_metafields").fetchone()[0],
        "collection_products": conn.execute("SELECT COUNT(*) FROM collection_products").fetchone()[0],
        "pages": conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
        "blogs": conn.execute("SELECT COUNT(*) FROM blogs").fetchone()[0],
        "blog_articles": conn.execute("SELECT COUNT(*) FROM blog_articles").fetchone()[0],
    }
    latest_run = conn.execute(
        """
        SELECT id, started_at, finished_at, status, products_synced, variants_synced, images_synced, metafields_synced
             , collections_synced, collection_metafields_synced, collection_products_synced, pages_synced
             , blogs_synced, blog_articles_synced
        FROM sync_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    payload = {"db_path": str(db_path), "counts": counts, "latest_run": dict(latest_run) if latest_run else None}
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Shopify catalog data into a local SQLite database.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-products", help="Sync all Shopify products into SQLite")
    sync_parser.add_argument("--page-size", type=int, default=50)

    collections_parser = subparsers.add_parser("sync-collections", help="Sync all Shopify collections into SQLite")
    collections_parser.add_argument("--page-size", type=int, default=50)

    pages_parser = subparsers.add_parser("sync-pages", help="Sync all Shopify pages into SQLite")
    pages_parser.add_argument("--page-size", type=int, default=50)

    blogs_parser = subparsers.add_parser("sync-blogs", help="Sync all Shopify blogs and articles into SQLite")
    blogs_parser.add_argument("--page-size", type=int, default=50)

    sync_all_parser = subparsers.add_parser("sync-all", help="Sync products, collections, pages, and blogs into SQLite")
    sync_all_parser.add_argument("--page-size", type=int, default=50)

    subparsers.add_parser("summary", help="Print database summary")

    probe_parser = subparsers.add_parser(
        "probe-blogs",
        help="Test Shopify API: list blogs and article counts (read-only, requires SHOPIFY_* env)",
    )
    probe_parser.add_argument("--page-size", type=int, default=50)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if args.command == "sync-products":
        print(json.dumps(sync_products(db_path, args.page_size), indent=2, ensure_ascii=True))
        threading.Thread(target=_bg_embed, args=(db_path, ("product",)), daemon=True).start()
        return
    if args.command == "sync-collections":
        print(json.dumps(sync_collections(db_path, args.page_size), indent=2, ensure_ascii=True))
        threading.Thread(target=_bg_embed, args=(db_path, ("collection",)), daemon=True).start()
        return
    if args.command == "sync-pages":
        print(json.dumps(sync_pages(db_path, args.page_size), indent=2, ensure_ascii=True))
        threading.Thread(target=_bg_embed, args=(db_path, ("page",)), daemon=True).start()
        return
    if args.command == "sync-blogs":
        print(json.dumps(sync_blogs(db_path, args.page_size), indent=2, ensure_ascii=True))
        threading.Thread(target=_bg_embed, args=(db_path, ("blog_article",)), daemon=True).start()
        return
    if args.command == "sync-all":
        print(json.dumps(sync_all(db_path, args.page_size), indent=2, ensure_ascii=True))
        return
    if args.command == "summary":
        print_summary(db_path)
        return
    if args.command == "probe-blogs":
        print(json.dumps(probe_shopify_blogs(args.page_size), indent=2, ensure_ascii=True))
        return
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
