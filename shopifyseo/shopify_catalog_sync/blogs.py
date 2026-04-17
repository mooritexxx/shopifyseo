import sqlite3
from pathlib import Path

from .db import (
    now_iso,
    json_dumps,
    tags_list_as_json,
    open_db,
    start_run,
    finish_run,
    fetch_all_blogs,
    fetch_blog_by_id,
    fetch_all_articles_for_blog,
    fetch_article_by_id,
)


def replace_blog_articles(conn: sqlite3.Connection, blog_shopify_id: str) -> None:
    conn.execute("DELETE FROM blog_articles WHERE blog_shopify_id = ?", (blog_shopify_id,))


def prune_deleted_blogs(conn: sqlite3.Connection, live_blogs: list[dict]) -> int:
    live_ids = {blog["id"] for blog in live_blogs}
    stale_rows = conn.execute("SELECT shopify_id, handle FROM blogs").fetchall()
    stale = [row for row in stale_rows if row[0] not in live_ids]
    for shopify_id, handle in stale:
        conn.execute(
            "DELETE FROM seo_recommendations WHERE object_type = 'blog' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM gsc_query_rows WHERE object_type = 'blog' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM gsc_query_dimension_rows WHERE object_type = 'blog' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM google_api_cache WHERE object_type = 'blog' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM seo_recommendations WHERE object_type = 'blog_article' AND object_handle LIKE ?",
            (f"{handle}/%",),
        )
        conn.execute(
            "DELETE FROM gsc_query_rows WHERE object_type = 'blog_article' AND object_handle LIKE ?",
            (f"{handle}/%",),
        )
        conn.execute(
            "DELETE FROM gsc_query_dimension_rows WHERE object_type = 'blog_article' AND object_handle LIKE ?",
            (f"{handle}/%",),
        )
        conn.execute(
            "DELETE FROM google_api_cache WHERE object_type = 'blog_article' AND object_handle LIKE ?",
            (f"{handle}/%",),
        )
        conn.execute("DELETE FROM blogs WHERE shopify_id = ?", (shopify_id,))
    return len(stale)


def upsert_blog(conn: sqlite3.Connection, blog: dict, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO blogs (
          shopify_id,
          title,
          handle,
          created_at,
          updated_at,
          comment_policy,
          tags_json,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
          title = excluded.title,
          handle = excluded.handle,
          created_at = excluded.created_at,
          updated_at = excluded.updated_at,
          comment_policy = excluded.comment_policy,
          tags_json = excluded.tags_json,
          raw_json = excluded.raw_json,
          synced_at = excluded.synced_at
        """,
        (
            blog["id"],
            blog["title"],
            blog["handle"],
            blog.get("createdAt") or "",
            blog.get("updatedAt") or "",
            str(blog.get("commentPolicy") or ""),
            tags_list_as_json(blog.get("tags")),
            json_dumps(blog),
            synced_at,
        ),
    )


def upsert_blog_article_from_admin_create(
    conn: sqlite3.Connection,
    article: dict,
    *,
    blog_handle: str,
    seo_title: str,
    seo_description: str,
) -> None:
    """Persist a newly created article from `articleCreate` response + known SEO strings.

    GraphQL returns no titleTag/descriptionTag on create; we merge local SEO values so the
    dashboard matches Shopify metafields.
    """
    blog = article.get("blog") or {}
    blog_shopify_id = str(blog.get("id") or "").strip()
    if not blog_shopify_id:
        raise ValueError("articleCreate response missing blog.id")

    enriched = dict(article)
    enriched["titleTag"] = {"value": seo_title} if (seo_title or "").strip() else {}
    enriched["descriptionTag"] = {"value": seo_description} if (seo_description or "").strip() else {}

    upsert_blog_article(conn, enriched, blog_shopify_id, blog_handle, now_iso())


def upsert_blog_article(
    conn: sqlite3.Connection,
    article: dict,
    blog_shopify_id: str,
    blog_handle: str,
    synced_at: str,
) -> None:
    title_tag = article.get("titleTag") or {}
    desc_tag = article.get("descriptionTag") or {}
    seo_title = title_tag.get("value") if isinstance(title_tag, dict) else ""
    seo_description = desc_tag.get("value") if isinstance(desc_tag, dict) else ""
    image = article.get("image")
    conn.execute(
        """
        INSERT INTO blog_articles (
          shopify_id,
          blog_shopify_id,
          blog_handle,
          title,
          handle,
          published_at,
          updated_at,
          is_published,
          body,
          summary,
          tags_json,
          author_name,
          seo_title,
          seo_description,
          image_json,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
          blog_shopify_id = excluded.blog_shopify_id,
          blog_handle = excluded.blog_handle,
          title = excluded.title,
          handle = excluded.handle,
          published_at = excluded.published_at,
          updated_at = excluded.updated_at,
          is_published = excluded.is_published,
          body = excluded.body,
          summary = excluded.summary,
          tags_json = excluded.tags_json,
          author_name = excluded.author_name,
          seo_title = excluded.seo_title,
          seo_description = excluded.seo_description,
          image_json = excluded.image_json,
          raw_json = excluded.raw_json,
          synced_at = excluded.synced_at
        """,
        (
            article["id"],
            blog_shopify_id,
            blog_handle,
            article["title"],
            article["handle"],
            article.get("publishedAt") or "",
            article.get("updatedAt") or "",
            1 if article.get("isPublished") else 0,
            article.get("body") or "",
            article.get("summary") or "",
            tags_list_as_json(article.get("tags")),
            ((article.get("author") or {}).get("name") or "") if isinstance(article.get("author"), dict) else "",
            seo_title or "",
            seo_description or "",
            json_dumps(image) if image else "",
            json_dumps(article),
            synced_at,
        ),
    )


def sync_article(db_path: Path, article_id: str) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        article = fetch_article_by_id(article_id)
        if not article:
            raise RuntimeError(f"Article not found in Shopify: {article_id}")
        blog = article.get("blog") or {}
        blog_shopify_id = str(blog.get("id") or "").strip()
        blog_handle = str(blog.get("handle") or "").strip()
        if not blog_shopify_id or not blog_handle:
            raise RuntimeError(f"Article {article_id} missing blog id/handle from Shopify")
        upsert_blog_article(conn, article, blog_shopify_id, blog_handle, synced_at)
        conn.commit()
        finish_run(conn, run_id, status="success", blog_articles_synced=1)
        return {
            "db_path": str(db_path),
            "blog_articles_synced": 1,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()


def sync_blogs(db_path: Path, page_size: int, progress_callback=None) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        blogs = fetch_all_blogs(page_size)
        if progress_callback is not None:
            progress_callback("blogs", 0, len(blogs))
        blog_count = 0
        article_count = 0
        for blog in blogs:
            upsert_blog(conn, blog, synced_at)
            replace_blog_articles(conn, blog["id"])
            articles = fetch_all_articles_for_blog(blog["id"], page_size)
            blog_handle = blog.get("handle") or ""
            articles_total_known = article_count + len(articles)
            if progress_callback is not None:
                progress_callback("blog_articles", article_count, articles_total_known)
            for article in articles:
                upsert_blog_article(conn, article, blog["id"], blog_handle, synced_at)
                article_count += 1
                if progress_callback is not None:
                    progress_callback("blog_articles", article_count, articles_total_known)
            blog_count += 1
            if progress_callback is not None:
                progress_callback("blogs", blog_count, len(blogs))
        pruned = prune_deleted_blogs(conn, blogs)
        conn.commit()
        finish_run(
            conn,
            run_id,
            status="success",
            blogs_synced=blog_count,
            blog_articles_synced=article_count,
        )
        return {
            "db_path": str(db_path),
            "blogs_synced": blog_count,
            "blog_articles_synced": article_count,
            "blogs_pruned": pruned,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()


def sync_blog(db_path: Path, blog_id: str, page_size: int = 50) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        blog = fetch_blog_by_id(blog_id)
        if not blog:
            raise RuntimeError(f"Blog not found in Shopify: {blog_id}")
        upsert_blog(conn, blog, synced_at)
        replace_blog_articles(conn, blog["id"])
        articles = fetch_all_articles_for_blog(blog["id"], page_size)
        blog_handle = blog.get("handle") or ""
        for article in articles:
            upsert_blog_article(conn, article, blog["id"], blog_handle, synced_at)
        conn.commit()
        finish_run(
            conn,
            run_id,
            status="success",
            blogs_synced=1,
            blog_articles_synced=len(articles),
        )
        return {
            "db_path": str(db_path),
            "blogs_synced": 1,
            "blog_articles_synced": len(articles),
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()
