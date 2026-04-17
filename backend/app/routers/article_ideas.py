import logging

from fastapi import APIRouter, HTTPException, status

import shopifyseo.dashboard_queries as dq
from backend.app.db import open_db_connection
from backend.app.schemas.article_ideas import (
    ArticleIdeaItem,
    ArticleIdeasPayload,
    BulkStatusRequest,
    IdeaPerformancePayload,
    UpdateIdeaStatusRequest,
)
from backend.app.schemas.common import SuccessResponse, success_response
from shopifyseo.dashboard_ai_engine_parts.generation import generate_article_ideas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/article-ideas", tags=["article-ideas"])

_VALID_STATUSES = {"idea", "approved", "published", "rejected"}


@router.get("", response_model=SuccessResponse[ArticleIdeasPayload])
def list_article_ideas():
    """Return all stored article ideas, newest first."""
    conn = open_db_connection()
    try:
        ideas = dq.fetch_article_ideas(conn)
    finally:
        conn.close()
    items = [ArticleIdeaItem.model_validate(idea) for idea in ideas]
    return success_response(ArticleIdeasPayload(items=items, total=len(items)))


@router.post("/generate", response_model=SuccessResponse[ArticleIdeasPayload])
def generate_ideas():
    """Run gap analysis + AI to produce new article ideas and store them."""
    conn = open_db_connection()
    try:
        try:
            ideas = generate_article_ideas(conn)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(exc),
            )
        dq.save_article_ideas(conn, ideas)
        try:
            from shopifyseo.embedding_store import sync_embeddings
            sync_embeddings(conn, object_type="article_idea")
        except Exception:
            logger.warning("Failed to sync embeddings after idea generation", exc_info=True)
        all_ideas = dq.fetch_article_ideas(conn)
    finally:
        conn.close()

    all_items = [ArticleIdeaItem.model_validate(i) for i in all_ideas]
    return success_response(ArticleIdeasPayload(items=all_items, total=len(all_items)))


@router.delete("/{idea_id}", response_model=SuccessResponse[dict])
def delete_idea(idea_id: int):
    """Permanently delete a single article idea by ID."""
    conn = open_db_connection()
    try:
        deleted = dq.delete_article_idea(conn, idea_id)
    finally:
        conn.close()
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea not found")
    return success_response({"deleted": idea_id})


@router.patch("/{idea_id}/approve", response_model=SuccessResponse[dict])
def approve_idea(idea_id: int):
    """Mark an idea as approved, moving it into the editorial queue."""
    conn = open_db_connection()
    try:
        updated = dq.update_article_idea_status(conn, idea_id, "approved")
    finally:
        conn.close()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea not found")
    return success_response({"id": idea_id, "status": "approved"})


@router.patch("/{idea_id}/status", response_model=SuccessResponse[dict])
def update_idea_status(idea_id: int, body: UpdateIdeaStatusRequest):
    """Update an idea's status. Valid values: idea, approved, published, rejected."""
    if body.new_status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status '{body.new_status}'. Must be one of: {sorted(_VALID_STATUSES)}",
        )
    conn = open_db_connection()
    try:
        updated = dq.update_article_idea_status(conn, idea_id, body.new_status)
    finally:
        conn.close()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea not found")
    return success_response({"id": idea_id, "status": body.new_status})


@router.patch("/bulk-status", response_model=SuccessResponse[dict])
def bulk_update_status(body: BulkStatusRequest):
    """Update status for multiple ideas at once."""
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status '{body.status}'. Must be one of: {sorted(_VALID_STATUSES)}",
        )
    conn = open_db_connection()
    try:
        count = dq.bulk_update_idea_status(conn, body.idea_ids, body.status)
    finally:
        conn.close()
    return success_response({"updated": count, "status": body.status})


@router.get("/{idea_id}/performance", response_model=SuccessResponse[IdeaPerformancePayload])
def get_idea_performance(idea_id: int):
    """Return aggregated performance across all articles linked to an idea."""
    conn = open_db_connection()
    try:
        perf = dq.compute_idea_performance(conn, idea_id)
    finally:
        conn.close()
    return success_response(IdeaPerformancePayload.model_validate(perf))
