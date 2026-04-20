import sqlite3
from pathlib import Path

from .db import (
    now_iso,
    json_dumps,
    open_db,
    start_run,
    finish_run,
    fetch_all_pages,
    fetch_page_by_id,
)
from .page_template_enrichment import enrich_pages_template_images


def upsert_page(conn: sqlite3.Connection, page: dict, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO pages (
          shopify_id,
          title,
          handle,
          updated_at,
          template_suffix,
          body,
          seo_title,
          seo_description,
          template_images_json,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
          title = excluded.title,
          handle = excluded.handle,
          updated_at = excluded.updated_at,
          template_suffix = excluded.template_suffix,
          body = excluded.body,
          seo_title = excluded.seo_title,
          seo_description = excluded.seo_description,
          raw_json = excluded.raw_json,
          synced_at = excluded.synced_at
        """,
        (
            page["id"],
            page["title"],
            page["handle"],
            page.get("updatedAt") or "",
            page.get("templateSuffix") or "",
            page.get("body") or "",
            ((page.get("titleTag") or {}).get("value")) or "",
            ((page.get("descriptionTag") or {}).get("value")) or "",
            None,
            json_dumps(page),
            synced_at,
        ),
    )


def sync_pages(
    db_path: Path,
    page_size: int,
    progress_callback=None,
    *,
    pages: list[dict] | None = None,
    queue_scope: str | None = None,
) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        if pages is None:
            pages = fetch_all_pages(page_size)
        else:
            pages = list(pages)
        if progress_callback is not None:
            progress_callback("pages", 0, len(pages))
        page_count = 0

        if queue_scope:
            from shopifyseo.dashboard_actions import _sync_queue as _sq

            _sq.sync_queue_seed(
                queue_scope,
                [
                    ("page", str(p.get("id") or "").strip(), (p.get("handle") or "")[:200])
                    for p in pages
                    if str(p.get("id") or "").strip()
                ],
            )

        for page in pages:
            pid = str(page.get("id") or "").strip()
            rk = _sq.catalog_sync_row_key("page", pid, (page.get("handle") or "")[:200]) if queue_scope and pid else ""
            if queue_scope and pid:
                _sq.sync_queue_mark_running(queue_scope, rk)
            ok = True
            err_msg: str | None = None
            try:
                upsert_page(conn, page, synced_at)
                page_count += 1
            except Exception as exc:
                ok = False
                err_msg = str(exc)
                raise
            finally:
                if queue_scope and pid:
                    _sq.sync_queue_mark_done(queue_scope, rk, ok, err_msg, pop_completed=ok)
            if progress_callback is not None:
                progress_callback("pages", page_count, len(pages))
        enrich_pages_template_images(conn)
        conn.commit()
        finish_run(conn, run_id, status="success", pages_synced=page_count)
        return {
            "db_path": str(db_path),
            "pages_synced": page_count,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()


def sync_page(db_path: Path, page_id: str) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        page = fetch_page_by_id(page_id)
        if not page:
            raise RuntimeError(f"Page not found in Shopify: {page_id}")
        upsert_page(conn, page, synced_at)
        enrich_pages_template_images(conn)
        conn.commit()
        finish_run(conn, run_id, status="success", pages_synced=1)
        return {
            "db_path": str(db_path),
            "pages_synced": 1,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()
