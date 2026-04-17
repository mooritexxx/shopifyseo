"""API endpoints for embedding management and semantic retrieval."""

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.db import open_db_connection
from backend.app.schemas.common import SuccessResponse, success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])


@router.get("/status", response_model=SuccessResponse[dict])
def embedding_status():
    """Return aggregate stats about the embedding store."""
    from shopifyseo.embedding_store import embedding_status as _embedding_status
    conn = open_db_connection()
    try:
        return success_response(_embedding_status(conn))
    except Exception as exc:
        logger.warning("Embedding status failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.post("/refresh", response_model=SuccessResponse[dict])
def refresh_embeddings():
    """Trigger a full embedding sync for all 9 entity types (runs in background)."""
    def _bg_refresh():
        try:
            from shopifyseo.embedding_store import sync_embeddings
            conn = open_db_connection()
            try:
                result = sync_embeddings(conn)
                logger.info("Embedding refresh complete: %s", result)
            finally:
                conn.close()
        except Exception:
            logger.warning("Background embedding refresh failed", exc_info=True)

    threading.Thread(target=_bg_refresh, daemon=True).start()
    return success_response({"status": "started", "message": "Embedding sync started in background"})


@router.get("/similar/{object_type}/{handle:path}", response_model=SuccessResponse[list])
def get_similar(
    object_type: str,
    handle: str,
    top_k: int = Query(default=5, ge=1, le=20),
):
    """Find objects semantically similar to the given object."""
    from shopifyseo.embedding_store import retrieve_related_by_handle
    conn = open_db_connection()
    try:
        results = retrieve_related_by_handle(conn, object_type, handle, top_k=top_k)
        return success_response(results)
    except Exception as exc:
        logger.warning("Similarity search failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.get("/semantic-keywords/{object_type}/{handle:path}", response_model=SuccessResponse[list])
def get_semantic_keywords(
    object_type: str,
    handle: str,
    top_k: int = Query(default=10, ge=1, le=30),
):
    """Find keywords semantically related to the given object."""
    from shopifyseo.embedding_store import find_semantic_keyword_matches
    conn = open_db_connection()
    try:
        results = find_semantic_keyword_matches(conn, object_type, handle, top_k=top_k)
        return success_response(results)
    except Exception as exc:
        logger.warning("Semantic keyword search failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.get("/competitive-gaps/{object_type}/{handle:path}", response_model=SuccessResponse[list])
def get_competitive_gaps(
    object_type: str,
    handle: str,
    top_k: int = Query(default=10, ge=1, le=20),
):
    """Find competitor pages covering similar topics to the given object."""
    from shopifyseo.embedding_store import find_competitive_gaps
    conn = open_db_connection()
    try:
        results = find_competitive_gaps(conn, object_type, handle, top_k=top_k)
        return success_response(results)
    except Exception as exc:
        logger.warning("Competitive gaps search failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.get("/cannibalization", response_model=SuccessResponse[list])
def get_cannibalization(
    threshold: float = Query(default=0.85, ge=0.5, le=1.0),
):
    """Find pairs of pages with high content and query embedding similarity."""
    from shopifyseo.embedding_store import find_cannibalization_candidates
    conn = open_db_connection()
    try:
        results = find_cannibalization_candidates(conn, threshold=threshold)
        return success_response(results)
    except Exception as exc:
        logger.warning("Cannibalization detection failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()
