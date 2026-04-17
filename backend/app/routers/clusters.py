import json
import logging
import math
import queue
import threading

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.db import open_db_connection
from backend.app.services.keyword_clustering import (
    enrich_clusters_with_coverage,
    generate_clusters,
    get_cluster_detail,
    get_match_options,
    load_clusters,
    update_cluster_match,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/keywords/clusters", tags=["clusters"])


def _sse_json_dumps(payload: dict) -> str:
    """JSON for SSE lines — avoid failing on NaN floats or odd types."""

    def _default(o: object) -> object:
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        return str(o)

    return json.dumps(payload, default=_default, ensure_ascii=True)


@router.get("", response_model=dict)
def get_clusters():
    conn = open_db_connection()
    try:
        data = load_clusters(conn)
        data = enrich_clusters_with_coverage(conn, data)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.post("/generate")
def generate_keyword_clusters():
    """Stream clustering progress via SSE, then emit the final result."""
    q: queue.Queue[str | None] = queue.Queue()

    def on_progress(msg: str) -> None:
        q.put(msg)

    result_holder: dict = {}
    error_holder: list[str] = []

    def worker() -> None:
        conn = open_db_connection()
        try:
            data = generate_clusters(conn, on_progress=on_progress)
            result_holder["data"] = data
        except Exception as exc:
            # Previously only RuntimeError was caught; JSONDecodeError, sqlite errors, etc.
            # left an empty error_holder and produced a useless generic SSE message.
            logger.exception("Keyword cluster generation failed")
            error_holder.append(str(exc).strip() or type(exc).__name__)
        finally:
            conn.close()
            q.put(None)  # sentinel

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream():
        while True:
            msg = q.get()
            if msg is None:
                break
            yield f"event: progress\ndata: {_sse_json_dumps({'message': msg})}\n\n"
        if error_holder:
            yield f"event: error\ndata: {_sse_json_dumps({'detail': error_holder[0]})}\n\n"
        elif "data" in result_holder:
            try:
                yield f"event: done\ndata: {_sse_json_dumps({'ok': True, 'data': result_holder['data']})}\n\n"
            except (TypeError, ValueError) as exc:
                logger.exception("Failed to serialize cluster generation result for SSE")
                yield (
                    f"event: error\ndata: {_sse_json_dumps({'detail': f'Clustering finished but response could not be sent: {exc}'})}\n\n"
                )
        else:
            yield f"event: error\ndata: {_sse_json_dumps({'detail': 'Clustering did not complete — check server logs for details.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/match-options", response_model=dict)
def get_cluster_match_options():
    """Return available pages for the match override dropdown."""
    conn = open_db_connection()
    try:
        options = get_match_options(conn)
        return {"ok": True, "data": {"options": options}}
    finally:
        conn.close()


@router.get("/{cluster_id}/detail", response_model=dict)
def get_cluster_detail_view(cluster_id: int):
    """Return cluster info with all discovered related URLs and keyword coverage."""
    conn = open_db_connection()
    try:
        data = get_cluster_detail(conn, cluster_id)
        return {"ok": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    finally:
        conn.close()


class MatchUpdateBody(BaseModel):
    cluster_id: int
    match_type: str
    match_handle: str
    match_title: str


@router.patch("/match", response_model=dict)
def patch_cluster_match(body: MatchUpdateBody):
    """Override the suggested_match for a single cluster."""
    conn = open_db_connection()
    try:
        data = update_cluster_match(
            conn,
            cluster_id=body.cluster_id,
            match_type=body.match_type,
            match_handle=body.match_handle,
            match_title=body.match_title,
        )
        return {"ok": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        conn.close()
