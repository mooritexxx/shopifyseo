import json
import queue
import threading
import time
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import shopifyseo.dashboard_queries as dq

from backend.app.schemas.blog import (
    AllArticlesPayload,
    ArticleCreateRequest,
    ArticleCreateResult,
    ArticleGenerateDraftRequest,
    ArticleGenerateDraftResult,
    BlogArticlesPayload,
    BlogListPayload,
)
from backend.app.routers import field_regen_errors
from backend.app.schemas.article_ideas import KeywordCoveragePayload
from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.dashboard import GscPeriodMode
from backend.app.schemas.content import ContentDetailPayload, ContentUpdatePayload
from backend.app.schemas.product import FieldRegenerateRequest, FieldRegenerateResult, ProductActionResult, ProductInspectionLinkPayload
from backend.app.db import get_db_path, open_db_connection
from backend.app.services.article_service import (
    get_blog_article_detail,
    get_blog_article_inspection_link,
    list_all_articles,
    list_blog_articles,
    list_blogs,
    update_blog_article,
)
from backend.app.services.dashboard_service import (
    refresh_object,
    regenerate_object_field,
    start_object_ai,
    start_object_field_regeneration,
)
from shopifyseo.dashboard_ai_engine_parts.generation import (
    ensure_link_titles,
    generate_article_draft,
    inject_article_body_images,
    try_prepare_article_images_bundle,
)
from shopifyseo.seo_slug import seo_article_slug, slugify_article_handle
from shopifyseo.shopify_admin import (
    create_article,
    query_blogs,
    update_article_body_html,
    update_article_featured_image,
)
from shopifyseo.dashboard_live_updates import publish_article
from shopifyseo.shopify_catalog_sync import upsert_blog_article_from_admin_create

router = APIRouter(prefix="/api", tags=["blogs"])


_ProgressFn = Callable[[str, str | None, str | None], None]


def _html_contains_img(html: str) -> bool:
    return "<img" in (html or "").lower()


def _attach_featured_image(
    article: dict,
    featured_url: str,
    fallback_alt: str,
    p: _ProgressFn,
) -> dict:
    """Retry-attach the featured image via articleUpdate when Shopify omits it on create."""
    p(
        "Shopify did not return a featured image on create — attaching with articleUpdate…",
        "attach",
        "start",
    )
    last_exc: RuntimeError | None = None
    for delay_s in (0, 2, 4):
        if delay_s:
            time.sleep(delay_s)
        try:
            upd = update_article_featured_image(article["id"], featured_url, fallback_alt)
        except SystemExit as exc:
            last_exc = RuntimeError(str(exc) or "Shopify image update failed")
            continue
        uerr = upd.get("userErrors") or []
        if uerr:
            last_exc = RuntimeError("; ".join(str(e.get("message") or e) for e in uerr))
            continue
        art2 = upd.get("article") or {}
        img2 = art2.get("image") or {}
        if isinstance(img2, dict) and (img2.get("url") or "").strip():
            article = {**article, **{k: v for k, v in art2.items() if v is not None}}
            article["image"] = img2
            p("Featured image attached in Shopify.", "attach", "done")
            last_exc = None
            break
    if last_exc is not None:
        p(f"Could not attach featured image: {last_exc}", "attach", "skipped")
    return article


def _sync_article_body_if_needed(article: dict, body_html: str, p: _ProgressFn) -> dict:
    """Re-sync the article body via articleUpdate if Shopify dropped the inline image."""
    shop_body = article.get("body") or ""
    if not (_html_contains_img(body_html) and not _html_contains_img(shop_body)):
        return article
    p(
        "Shopify dropped the inline hero from the saved body — syncing body via articleUpdate…",
        "body",
        "start",
    )
    try:
        upd_b = update_article_body_html(article["id"], body_html)
    except SystemExit as exc:
        p(f"Could not sync article body: {exc}", "body", "skipped")
        return article
    berr = upd_b.get("userErrors") or []
    if berr:
        p("Body update: " + "; ".join(str(e.get("message") or e) for e in berr), "body", "skipped")
    else:
        art_b = upd_b.get("article") or {}
        article["body"] = art_b.get("body") or body_html
        p("Article body updated with hero image.", "body", "done")
    return article


def _persist_article_locally(
    article: dict,
    payload: ArticleGenerateDraftRequest,
    generated: dict,
    p: _ProgressFn,
) -> None:
    """Upsert the Shopify article into the local DB and link any pending idea."""
    p("Saving article to local database…", "local", "start")
    conn = open_db_connection()
    try:
        upsert_blog_article_from_admin_create(
            conn,
            article,
            blog_handle=payload.blog_handle,
            seo_title=generated["seo_title"],
            seo_description=generated["seo_description"],
        )
        if payload.idea_id is not None:
            dq.link_idea_to_article(
                conn,
                idea_id=payload.idea_id,
                article_handle=article["handle"],
                blog_handle=payload.blog_handle,
                shopify_article_id=article["id"],
                angle_label=getattr(payload, "angle_label", ""),
            )
            idea_row = conn.execute(
                "SELECT primary_keyword, supporting_keywords FROM article_ideas WHERE id = ?",
                (payload.idea_id,),
            ).fetchone()
            if idea_row:
                import json as _json
                try:
                    sup_kw = _json.loads(idea_row[1] or "[]")
                except (ValueError, TypeError):
                    sup_kw = []
                dq.save_article_target_keywords(
                    conn,
                    blog_handle=payload.blog_handle,
                    article_handle=article["handle"],
                    primary_keyword=idea_row[0] or "",
                    supporting_keywords=sup_kw if isinstance(sup_kw, list) else [],
                )
        conn.commit()
    finally:
        conn.close()
    p("Article saved — opening detail…", "local", "done")


def _run_generate_article_draft(
    payload: ArticleGenerateDraftRequest,
    *,
    on_progress: Callable[[dict], None] | None = None,
) -> ArticleGenerateDraftResult:
    """AI draft → Shopify `articleCreate` → upsert local `blog_articles` so detail routes work."""

    def p(message: str, phase: str | None = None, state: str | None = None, **extra: object) -> None:
        if not on_progress:
            return
        row: dict = {"message": message}
        if phase is not None:
            row["phase"] = phase
        if state is not None:
            row["state"] = state
        row.update(extra)
        on_progress(row)

    conn = open_db_connection()
    try:
        cluster_id = None
        if payload.idea_id is not None:
            idea_row = conn.execute(
                "SELECT linked_cluster_id FROM article_ideas WHERE id = ?",
                (payload.idea_id,),
            ).fetchone()
            if idea_row and idea_row[0] is not None:
                cluster_id = int(idea_row[0])

        generated = generate_article_draft(
            conn,
            topic=payload.topic,
            keywords=payload.keywords or [],
            author_name=payload.author_name,
            linked_cluster_id=cluster_id,
            on_progress=on_progress,
        )

        p("Starting images: featured cover + per-section body images…", "image", "start")
        featured_url, featured_alt, body_images, image_notes = try_prepare_article_images_bundle(
            conn,
            title=generated["title"],
            topic=payload.topic,
            body_html=generated["body"],
            on_step=p,
        )
        for note in image_notes:
            p(note, "image", "running")
        p(
            f"Featured + {len(body_images)} section image{'s' if len(body_images) != 1 else ''} ready for Shopify."
            if (featured_url and body_images)
            else "Featured cover ready; section images skipped or failed."
            if featured_url
            else "No images — skipped or failed.",
            "image",
            "done" if featured_url else "skipped",
        )

        body_html = generated["body"]
        if body_images:
            p(f"Inserting {len(body_images)} section images into article body HTML…", "body", "start")
            body_html = inject_article_body_images(body_html, body_images)
            p(f"{len(body_images)} section image{'s' if len(body_images) != 1 else ''} injected into body.", "body", "done")
        else:
            p("Skipping inline body images (none generated).", "body", "skipped")

        body_html = ensure_link_titles(body_html, conn)

        raw_slug = (payload.slug_hint or "").strip()
        if raw_slug:
            handle = slugify_article_handle(raw_slug)
        else:
            # No slug hint — derive an SEO-optimised slug from the AI title + keywords
            kw_list = [
                (k["keyword"] if isinstance(k, dict) else str(k))
                for k in (payload.keywords or [])
            ]
            handle = seo_article_slug(generated["title"], keywords=kw_list)

        p("Creating draft article in Shopify…", "shopify", "start")
        try:
            result = create_article(
                blog_id=payload.blog_id,
                title=generated["title"],
                body_html=body_html,
                author_name=payload.author_name or "",
                handle=handle,
                summary=generated["seo_description"],
                tags=None,
                is_published=False,
                seo_title=generated["seo_title"],
                seo_description=generated["seo_description"],
                image_url=featured_url or "",
                image_alt=featured_alt or "",
            )
        except SystemExit as exc:
            raise RuntimeError(str(exc) or "Shopify request failed") from exc

        errors = result.get("userErrors", [])
        if errors:
            raise RuntimeError("; ".join(e["message"] for e in errors))

        article = result["article"]
        created_img_url = ((article.get("image") or {}) if isinstance(article.get("image"), dict) else {}).get("url", "").strip()
        if (featured_url or "").strip().startswith("https://") and not created_img_url:
            article = _attach_featured_image(article, (featured_url or "").strip(), featured_alt or generated["title"], p)
        else:
            p(
                "Featured image present on created article." if created_img_url else "No featured image for this draft.",
                "attach",
                "done" if created_img_url else "skipped",
            )

        article = _sync_article_body_if_needed(article, body_html, p)
        p("Draft created in Shopify.", "shopify", "done")
    finally:
        conn.close()

    _persist_article_locally(article, payload, generated, p)

    return ArticleGenerateDraftResult(
        id=article["id"],
        title=article["title"],
        handle=article["handle"],
        blog_handle=payload.blog_handle,
        blog_title=article.get("blog", {}).get("title", ""),
        is_published=article["isPublished"],
        seo_title=generated["seo_title"],
        seo_description=generated["seo_description"],
    )


@router.get("/articles", response_model=SuccessResponse[AllArticlesPayload])
def get_all_articles():
    return success_response(AllArticlesPayload.model_validate(list_all_articles()))


@router.get("/articles/{blog_handle}/{article_handle}", response_model=SuccessResponse[ContentDetailPayload])
def article_detail(blog_handle: str, article_handle: str, gsc_period: GscPeriodMode = "mtd"):
    detail = get_blog_article_detail(blog_handle, article_handle, gsc_period=gsc_period)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    return success_response(detail)


@router.get(
    "/articles/{blog_handle}/{article_handle}/keyword-coverage",
    response_model=SuccessResponse[KeywordCoveragePayload],
)
def article_keyword_coverage(blog_handle: str, article_handle: str):
    """Return target-keyword coverage report for a specific article."""
    conn = open_db_connection()
    try:
        data = dq.compute_keyword_coverage(conn, blog_handle, article_handle)
    finally:
        conn.close()
    return success_response(KeywordCoveragePayload.model_validate(data))


@router.post("/articles/{blog_handle}/{article_handle}/update", response_model=SuccessResponse[ProductActionResult])
def article_update(
    blog_handle: str, article_handle: str, payload: ContentUpdatePayload, gsc_period: GscPeriodMode = "mtd"
):
    ok, message = update_blog_article(blog_handle, article_handle, payload.model_dump())
    if not ok:
        if message == "Article not found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=message)
    detail = get_blog_article_detail(blog_handle, article_handle, gsc_period=gsc_period)
    return success_response({"message": message, "result": detail})


class _PublishRequest(BaseModel):
    is_published: bool


@router.patch(
    "/articles/{blog_handle}/{article_handle}/publish",
    response_model=SuccessResponse[ProductActionResult],
)
def article_publish(blog_handle: str, article_handle: str, payload: _PublishRequest):
    """Publish or unpublish (hide) a Shopify article."""
    conn = open_db_connection()
    try:
        row = conn.execute(
            "SELECT shopify_id FROM blog_articles WHERE blog_handle = ? AND handle = ?",
            (blog_handle, article_handle),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    shopify_id = row["shopify_id"]
    try:
        publish_article(get_db_path(), shopify_id, is_published=payload.is_published)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    action = "published" if payload.is_published else "unpublished"
    detail = get_blog_article_detail(blog_handle, article_handle)
    return success_response({"message": f"Article {action}", "result": detail})


@router.post(
    "/articles/{blog_handle}/{article_handle}/inspection-link",
    response_model=SuccessResponse[ProductInspectionLinkPayload],
)
def article_inspection_link(blog_handle: str, article_handle: str):
    ok, href = get_blog_article_inspection_link(blog_handle, article_handle)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=href)
    return success_response({"href": href})


@router.post("/articles/{blog_handle}/{article_handle}/refresh", response_model=SuccessResponse[ProductActionResult])
def article_refresh(
    blog_handle: str, article_handle: str, payload: dict | None = None, gsc_period: GscPeriodMode = "mtd"
):
    composite = dq.blog_article_composite_handle(blog_handle, article_handle)
    step = payload.get("step") if payload else None
    ok, result = refresh_object("blog_article", composite, step, gsc_period=gsc_period)
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result.get("message", "Refresh failed"))
    return success_response(result)


@router.post("/articles/{blog_handle}/{article_handle}/generate-ai", response_model=SuccessResponse[ProductActionResult])
def article_generate_ai(blog_handle: str, article_handle: str):
    composite = dq.blog_article_composite_handle(blog_handle, article_handle)
    ok, message, state = start_object_ai("blog_article", composite)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.post("/articles/{blog_handle}/{article_handle}/regenerate-field", response_model=SuccessResponse[FieldRegenerateResult])
def article_regenerate_field(blog_handle: str, article_handle: str, payload: FieldRegenerateRequest):
    composite = dq.blog_article_composite_handle(blog_handle, article_handle)
    with field_regen_errors():
        result = regenerate_object_field("blog_article", composite, payload.field, payload.accepted_fields)
        return success_response(result)


@router.post("/articles/{blog_handle}/{article_handle}/regenerate-field/start", response_model=SuccessResponse[ProductActionResult])
def article_regenerate_field_start(blog_handle: str, article_handle: str, payload: FieldRegenerateRequest):
    composite = dq.blog_article_composite_handle(blog_handle, article_handle)
    with field_regen_errors():
        ok, message, state = start_object_field_regeneration("blog_article", composite, payload.field, payload.accepted_fields)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return success_response({"message": message, "state": state})


@router.get("/blogs", response_model=SuccessResponse[BlogListPayload])
def get_blogs():
    return success_response(BlogListPayload.model_validate(list_blogs()))


@router.get("/blogs/shopify-ids", response_model=SuccessResponse[list[dict]])
def get_blog_shopify_ids():
    """Return Shopify GIDs for all blogs (needed to create articles)."""
    try:
        blogs = query_blogs()
        return success_response(blogs)
    except SystemExit as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/blogs/{blog_handle}/articles", response_model=SuccessResponse[BlogArticlesPayload])
def get_blog_articles(blog_handle: str):
    payload = list_blog_articles(blog_handle)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog not found")
    return success_response(BlogArticlesPayload.model_validate(payload))


@router.post("/articles/generate-draft", response_model=SuccessResponse[ArticleGenerateDraftResult])
def generate_new_article_draft(payload: ArticleGenerateDraftRequest):
    """Use AI to write a brand-new article draft, then publish it to Shopify as a draft."""
    try:
        result = _run_generate_article_draft(payload)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return success_response(result)


@router.post("/articles/generate-draft-stream")
def generate_new_article_draft_stream(payload: ArticleGenerateDraftRequest):
    """Same as generate-draft but streams progress events (SSE) for the UI."""

    q: queue.Queue[tuple[str, dict] | None] = queue.Queue()

    def worker() -> None:
        try:
            def on_progress(data: dict) -> None:
                q.put(("progress", data))

            out = _run_generate_article_draft(payload, on_progress=on_progress)
            q.put(("done", out.model_dump()))
        except RuntimeError as exc:
            q.put(("error", {"detail": str(exc)}))
        except SystemExit as exc:
            q.put(("error", {"detail": str(exc) or "Shopify request failed"}))
        except Exception as exc:
            q.put(("error", {"detail": str(exc)}))
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            item = q.get()
            if item is None:
                break
            kind, data = item
            yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/articles/create", response_model=SuccessResponse[ArticleCreateResult])
def create_draft_article(payload: ArticleCreateRequest):
    """Create a draft (or published) blog article on Shopify."""
    try:
        result = create_article(
            blog_id=payload.blog_id,
            title=payload.title,
            body_html=payload.body_html,
            author_name=payload.author_name,
            handle=payload.handle,
            summary=payload.summary,
            tags=payload.tags or None,
            is_published=payload.is_published,
        )
    except SystemExit as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    errors = result.get("userErrors", [])
    if errors:
        messages = "; ".join(e["message"] for e in errors)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=messages)

    article = result["article"]
    return success_response(
        ArticleCreateResult(
            id=article["id"],
            title=article["title"],
            handle=article["handle"],
            blog_title=article.get("blog", {}).get("title", ""),
            is_published=article["isPublished"],
        )
    )
