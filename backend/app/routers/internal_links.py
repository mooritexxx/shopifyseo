"""API endpoints for the internal linking engine."""

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.db import open_db_connection
from backend.app.schemas.common import SuccessResponse, success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal-links", tags=["internal-links"])


def _base_url(conn) -> str:
    from shopifyseo.dashboard_queries._urls import _base_store_url

    return _base_store_url(conn)


@router.get("/summary", response_model=SuccessResponse[dict])
def summary():
    conn = open_db_connection()
    try:
        total_links = conn.execute("SELECT COUNT(*) FROM internal_links").fetchone()[0]
        counts = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM link_suggestions GROUP BY status"
            ).fetchall()
        }
        from shopifyseo.internal_links.pipeline import _orphan_targets, internal_link_sync_progress

        return success_response({
            "total_links": total_links,
            "orphan_count": len(_orphan_targets(conn)),
            "suggested": counts.get("suggested", 0),
            "applied": counts.get("applied", 0),
            "dismissed": counts.get("dismissed", 0),
            "progress": internal_link_sync_progress(),
        })
    finally:
        conn.close()


@router.get("/orphans", response_model=SuccessResponse[list])
def orphans():
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.pipeline import _orphan_targets

        items = sorted(_orphan_targets(conn))
        return success_response([
            {"object_type": t, "handle": h} for t, h in items
        ])
    finally:
        conn.close()


@router.get("/suggestions", response_model=SuccessResponse[list])
def suggestions(
    source_type: str | None = Query(default=None),
    source_handle: str | None = Query(default=None),
    status_filter: str = Query(default="suggested", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    conn = open_db_connection()
    try:
        sql = "SELECT * FROM link_suggestions WHERE status = ?"
        params: list = [status_filter]
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        if source_handle:
            sql += " AND source_handle = ?"
            params.append(source_handle)
        sql += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return success_response(rows)
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/generate-anchor", response_model=SuccessResponse[dict])
def generate_anchor(suggestion_id: int):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.ai_weave import generate_ai_anchor

        return success_response(generate_ai_anchor(conn, suggestion_id, base_url=_base_url(conn)))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Anchor generation failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/apply", response_model=SuccessResponse[dict])
def apply(suggestion_id: int):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import apply_suggestion

        return success_response(apply_suggestion(conn, suggestion_id, base_url=_base_url(conn)))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Apply suggestion failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuccessResponse[dict])
def dismiss(suggestion_id: int):
    conn = open_db_connection()
    try:
        cur = conn.execute(
            "UPDATE link_suggestions SET status = 'dismissed' WHERE id = ? AND status = 'suggested'",
            (suggestion_id,),
        )
        conn.commit()
        if not cur.rowcount:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found or not pending")
        return success_response({"status": "dismissed"})
    finally:
        conn.close()


@router.post("/rebuild", response_model=SuccessResponse[dict])
def rebuild():
    def _bg():
        try:
            from shopifyseo.internal_links.pipeline import generate_link_suggestions

            conn = open_db_connection()
            try:
                generate_link_suggestions(conn)
            finally:
                conn.close()
        except Exception:
            logger.warning("Internal link rebuild failed", exc_info=True)

    threading.Thread(target=_bg, daemon=True).start()
    return success_response({"status": "started"})
