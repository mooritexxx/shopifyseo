"""API endpoints for the internal linking engine."""

import base64
import json
import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from shopifyseo.internal_links.safety import LinkConflict, AI_TYPES_KEY, SOURCE_TYPES, ai_enabled_types
from shopifyseo.internal_links.manual_weave import ManualWeaveRejected

from backend.app.db import open_db_connection
from backend.app.schemas.common import PaginatedSuccessResponse, SuccessResponse, success_response, paginated_response

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

        items = _orphan_targets(conn)
        return success_response([
            {"object_type": t, "handle": h, "gsc_clicks": c, "gsc_impressions": i}
            for t, h, c, i in items
        ])
    finally:
        conn.close()


@router.get("/graph-stats", response_model=SuccessResponse[dict])
def graph_stats(
    object_type: str | None = Query(default=None),
    handle: str | None = Query(default=None),
):
    """Per-entity inbound/outbound link counts."""
    conn = open_db_connection()
    try:
        if object_type and handle:
            inbound = conn.execute(
                "SELECT COUNT(*) FROM internal_links WHERE target_type = ? AND target_handle = ?",
                (object_type, handle),
            ).fetchone()[0]
            outbound = conn.execute(
                "SELECT COUNT(*) FROM internal_links WHERE source_type = ? AND source_handle = ?",
                (object_type, handle),
            ).fetchone()[0]
            return success_response({
                "object_type": object_type,
                "handle": handle,
                "inbound": inbound,
                "outbound": outbound,
            })
        outbound_map: dict[tuple[str, str], int] = {}
        inbound_map: dict[tuple[str, str], int] = {}
        for row in conn.execute(
            "SELECT source_type, source_handle, COUNT(*) AS c FROM internal_links GROUP BY 1, 2"
        ).fetchall():
            outbound_map[(row["source_type"], row["source_handle"])] = row["c"]
        for row in conn.execute(
            "SELECT target_type, target_handle, COUNT(*) AS c FROM internal_links GROUP BY 1, 2"
        ).fetchall():
            inbound_map[(row["target_type"], row["target_handle"])] = row["c"]
        all_keys = set(outbound_map.keys()) | set(inbound_map.keys())
        stats: list[dict] = []
        for ot, h in all_keys:
            ob = outbound_map.get((ot, h), 0)
            ib = inbound_map.get((ot, h), 0)
            stats.append({"object_type": ot, "handle": h, "outbound": ob, "inbound": ib})
        stats.sort(key=lambda x: x["outbound"] + x["inbound"], reverse=True)
        stats = stats[:500]
        return success_response({"entities": stats})
    finally:
        conn.close()


def _decode_cursor(cursor: str | None) -> tuple[float, int] | None:
    """Decode a base64-encoded JSON cursor to (score, id).

    Returns None if cursor is None or invalid.
    Raises HTTPException(400) for malformed cursors.
    """
    if cursor is None:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(decoded)
        return (float(data["s"]), int(data["i"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed cursor")


def _encode_cursor(score: float, id_: int) -> str:
    """Encode (score, id) as a base64 JSON cursor."""
    return base64.urlsafe_b64encode(json.dumps({"s": score, "i": id_}).encode()).decode()


@router.get("/suggestions", response_model=PaginatedSuccessResponse[list])
def suggestions(
    response: Response,
    source_type: str | None = Query(default=None),
    source_handle: str | None = Query(default=None),
    status_filter: str = Query(default="suggested", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, description="Opaque cursor for keyset pagination; when provided, offset is ignored."),
):
    """List link suggestions with pagination.

    Supports two pagination modes:
    - **Offset-based**: Use `offset` for random access. Rows may shift if data changes between requests.
    - **Cursor-based**: Use `cursor` (from `meta.next_cursor`) for drift-free batch walking while rows
      are being applied or dismissed. When `cursor` is provided, `offset` is ignored.

    The response `meta.total` always reflects the full filtered count (not affected by cursor).
    """
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.anchors import get_weak_anchor_warning

        # Build WHERE clause (reused for both data and count queries)
        where_clauses = ["status = ?"]
        where_params: list = [status_filter]
        if source_type:
            where_clauses.append("source_type = ?")
            where_params.append(source_type)
        if source_handle:
            where_clauses.append("source_handle = ?")
            where_params.append(source_handle)
        where_sql = " AND ".join(where_clauses)

        # Count total matching rows (ignoring cursor/offset)
        count_sql = f"SELECT COUNT(*) FROM link_suggestions WHERE {where_sql}"
        total = conn.execute(count_sql, where_params).fetchone()[0]

        # Set X-Total-Count header
        response.headers["X-Total-Count"] = str(total)

        # Build data query
        cursor_data = _decode_cursor(cursor)
        cursor_mode = cursor_data is not None
        if cursor_mode:
            # Keyset pagination: ignore offset, filter by cursor position
            # Fetch limit+1 to determine has_more without relying on offset
            cursor_score, cursor_id = cursor_data
            where_clauses.append("(score < ? OR (score = ? AND id > ?))")
            where_params.extend([cursor_score, cursor_score, cursor_id])
            where_sql = " AND ".join(where_clauses)
            fetch_limit = limit + 1
            effective_offset = 0
        else:
            fetch_limit = limit
            effective_offset = offset

        data_sql = f"SELECT * FROM link_suggestions WHERE {where_sql} ORDER BY score DESC, id ASC LIMIT ? OFFSET ?"
        data_params = where_params + [fetch_limit, effective_offset]

        rows = []
        enabled_types = ai_enabled_types(conn)
        operations = {r["suggestion_id"]: dict(r) for r in conn.execute(
            "SELECT suggestion_id, status FROM link_body_snapshots WHERE status IN "
            "('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')"
        )}
        for r in conn.execute(data_sql, data_params).fetchall():
            row_dict = dict(r)
            row_dict["ai_enabled"] = row_dict["source_type"] in enabled_types
            row_dict["pending_operation"] = operations.get(row_dict["id"], {}).get("status")
            if row_dict.get("kind") == "phrase_wrap":
                row_dict["weak_anchor_warning"] = get_weak_anchor_warning(row_dict.get("anchor_phrase"))
            else:
                row_dict["weak_anchor_warning"] = None
            rows.append(row_dict)

        # In cursor mode, determine has_more by whether we got more than limit rows
        has_more_override = None
        if cursor_mode:
            has_more_override = len(rows) > limit
            if has_more_override:
                rows = rows[:limit]  # Trim to requested limit

        # Build next_cursor from last row if there are more rows
        next_cursor = None
        if rows:
            last = rows[-1]
            next_cursor = _encode_cursor(last["score"], last["id"])

        return paginated_response(rows, total, limit, offset, next_cursor, has_more_override)
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/generate-anchor", response_model=SuccessResponse[dict])
def generate_anchor(suggestion_id: int):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.ai_weave import generate_ai_anchor

        return success_response(generate_ai_anchor(conn, suggestion_id, base_url=_base_url(conn)))
    except LinkConflict as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": {"code": "link_conflict", **exc.detail}})
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Anchor generation failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


class ApplyRequest(BaseModel):
    preview_token: str = ""


class ManualWeaveRequest(BaseModel):
    original_sentence: str
    replacement_sentence: str


@router.post("/suggestions/{suggestion_id}/manual-weave", response_model=SuccessResponse[dict])
def manual_weave(suggestion_id: int, payload: ManualWeaveRequest):
    """Submit a hand-written sentence addition for an ai_woven suggestion.
    
    This endpoint allows manually providing the edit text without running AI generation.
    The replacement_sentence must start with the original_sentence verbatim (append-only),
    and must contain exactly one <a> link pointing to the suggestion's target.
    
    Returns:
        - On success: preview data including preview_token, plus edit, existing_link_count, link_cap
        - 400: Content validation failed (ManualWeaveRejected with gaps)
        - 409: State/drift conflict (LinkConflict)
        - 500: Internal error
    """
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.manual_weave import submit_manual_weave
        
        result = submit_manual_weave(
            conn,
            suggestion_id,
            base_url=_base_url(conn),
            original_sentence=payload.original_sentence,
            replacement_sentence=payload.replacement_sentence,
        )
        return success_response(result)
    except LinkConflict as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": {"code": "link_conflict", **exc.detail}})
    except ManualWeaveRejected as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": {"code": "manual_weave_rejected", "message": str(exc), "gaps": exc.gaps}},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Manual weave failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/apply", response_model=SuccessResponse[dict])
def apply(suggestion_id: int, payload: ApplyRequest):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import apply_suggestion

        return success_response(apply_suggestion(conn, suggestion_id, base_url=_base_url(conn), preview_token_value=payload.preview_token))
    except LinkConflict as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": {"code": "link_conflict", **exc.detail}})
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
        pending = conn.execute("SELECT 1 FROM link_body_snapshots WHERE suggestion_id = ? AND status IN "
                               "('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')", (suggestion_id,)).fetchone()
        if pending:
            raise HTTPException(status_code=409, detail="Reconcile the unfinished write before dismissing this suggestion.")
        # Get suggestion data before updating for event logging
        sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not sug or sug["status"] != "suggested":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found or not pending")
        
        changed = conn.execute(
            "UPDATE link_suggestions SET status = 'dismissed' WHERE id = ? AND status = 'suggested' "
            "AND NOT EXISTS (SELECT 1 FROM link_body_snapshots WHERE suggestion_id = link_suggestions.id "
            "AND status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation'))",
            (suggestion_id,),
        ).rowcount
        if not changed:
            conn.rollback()
            raise HTTPException(status_code=409, detail="The suggestion changed or has an unfinished write. Refresh its status.")
        
        # Phase D: Log the dismiss event
        from shopifyseo.internal_links.apply import _log_suggestion_event
        _log_suggestion_event(conn, sug, "dismiss")
        
        conn.commit()
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


@router.get("/applied", response_model=SuccessResponse[list])
def applied_list(
    source_type: str | None = Query(default=None),
    source_handle: str | None = Query(default=None),
    problems_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List applied suggestions with presence in the saved catalog body."""
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import check_link_present_in_body, _SOURCE_META
        from shopifyseo.dashboard_queries._urls import object_url_with_base

        base_url = _base_url(conn)
        
        sql = "SELECT * FROM link_suggestions WHERE status = 'applied'"
        params: list = []
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        if source_handle:
            sql += " AND source_handle = ?"
            params.append(source_handle)
        sql += " ORDER BY applied_at DESC LIMIT ?"
        params.append(limit)
        
        rows = []
        for r in conn.execute(sql, params).fetchall():
            row_dict = dict(r)
            snapshot = conn.execute("SELECT status FROM link_body_snapshots WHERE suggestion_id = ? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            row_dict["can_undo"] = bool(snapshot and snapshot["status"] == "applied")
            row_dict["pending_operation"] = snapshot["status"] if snapshot and snapshot["status"] in ("undo_prepared", "undo_needs_reconciliation") else None

            # Check if link is present in current body
            src_type = row_dict["source_type"]
            src_handle = row_dict["source_handle"]
            tgt_type = row_dict["target_type"]
            tgt_handle = row_dict["target_handle"]
            
            # Get current body
            try:
                table, where, body_col, _cols = _SOURCE_META[src_type]
                if src_type == "blog_article":
                    blog_h, _, article_h = src_handle.partition("/")
                    body_row = conn.execute(
                        f"SELECT {body_col} FROM {table} WHERE blog_handle = ? AND handle = ?",
                        (blog_h, article_h)
                    ).fetchone()
                else:
                    body_row = conn.execute(
                        f"SELECT {body_col} FROM {table} WHERE handle = ?",
                        (src_handle,)
                    ).fetchone()
                
                current_body = body_row[body_col] if body_row else ""
                href = object_url_with_base(base_url, tgt_type, tgt_handle)
                row_dict["live_present"] = check_link_present_in_body(current_body, href)
                row_dict["href"] = href
            except Exception:
                row_dict["live_present"] = None
                row_dict["href"] = None
            
            if problems_only and row_dict.get("live_present", True):
                continue
                
            rows.append(row_dict)
        
        return success_response(rows)
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/undo", response_model=SuccessResponse[dict])
def undo(suggestion_id: int):
    """Restore an applied suggestion's backup if the live body is unchanged."""
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import undo_suggestion

        return success_response(undo_suggestion(conn, suggestion_id, base_url=_base_url(conn)))
    except LinkConflict as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": {"code": "link_conflict", **exc.detail}})
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Undo suggestion failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    finally:
        conn.close()


@router.get("/suggestions/{suggestion_id}/preview", response_model=SuccessResponse[dict])
@router.post("/suggestions/{suggestion_id}/preview", response_model=SuccessResponse[dict])
def preview(suggestion_id: int):
    from shopifyseo.internal_links.apply import preview_suggestion
    conn = open_db_connection()
    try:
        return success_response(preview_suggestion(conn, suggestion_id, _base_url(conn)))
    except Exception:
        logger.warning("Live preview failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not read Shopify for preview. Nothing was written.")
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/reconcile", response_model=SuccessResponse[dict])
def reconcile(suggestion_id: int):
    from shopifyseo.internal_links.apply import reconcile_suggestion
    conn = open_db_connection()
    try:
        return success_response(reconcile_suggestion(conn, suggestion_id, _base_url(conn)))
    except LinkConflict as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": {"code": "link_conflict", **exc.detail}})
    except Exception:
        logger.warning("Reconciliation failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Could not reconcile with Shopify. The backup is retained.")
    finally:
        conn.close()


@router.get("/graph-map", response_model=SuccessResponse[dict])
def graph_map(
    focus_type: str | None = Query(default=None),
    focus_handle: str | None = Query(default=None),
    max_nodes: int = Query(default=100, ge=10, le=500),
):
    """Get graph data in a format suitable for visualization (nodes + edges).
    
    If focus_type and focus_handle are provided, returns the neighborhood of that node.
    Otherwise, returns the top N nodes by total degree (inbound + outbound).
    """
    conn = open_db_connection()
    try:
        nodes: dict[tuple[str, str], dict] = {}
        edges: list[dict] = []
        
        if focus_type and focus_handle:
            # Get neighborhood of the focus node
            # First, add the focus node
            focus_key = (focus_type, focus_handle)
            nodes[focus_key] = {
                "id": f"{focus_type}:{focus_handle}",
                "object_type": focus_type,
                "handle": focus_handle,
                "inbound": 0,
                "outbound": 0,
                "is_focus": True,
            }
            
            # Get outbound links from focus
            for r in conn.execute(
                "SELECT target_type, target_handle, anchor_text FROM internal_links "
                "WHERE source_type = ? AND source_handle = ?",
                (focus_type, focus_handle)
            ).fetchall():
                key = (r["target_type"], r["target_handle"])
                if key not in nodes:
                    nodes[key] = {
                        "id": f"{r['target_type']}:{r['target_handle']}",
                        "object_type": r["target_type"],
                        "handle": r["target_handle"],
                        "inbound": 0,
                        "outbound": 0,
                        "is_focus": False,
                    }
                nodes[focus_key]["outbound"] += 1
                nodes[key]["inbound"] += 1
                edges.append({
                    "source": nodes[focus_key]["id"],
                    "target": nodes[key]["id"],
                    "anchor_text": r["anchor_text"],
                })
            
            # Get inbound links to focus
            for r in conn.execute(
                "SELECT source_type, source_handle, anchor_text FROM internal_links "
                "WHERE target_type = ? AND target_handle = ?",
                (focus_type, focus_handle)
            ).fetchall():
                key = (r["source_type"], r["source_handle"])
                if key not in nodes:
                    nodes[key] = {
                        "id": f"{r['source_type']}:{r['source_handle']}",
                        "object_type": r["source_type"],
                        "handle": r["source_handle"],
                        "inbound": 0,
                        "outbound": 0,
                        "is_focus": False,
                    }
                nodes[key]["outbound"] += 1
                nodes[focus_key]["inbound"] += 1
                edges.append({
                    "source": nodes[key]["id"],
                    "target": nodes[focus_key]["id"],
                    "anchor_text": r["anchor_text"],
                })
        else:
            # Get top nodes by degree
            degree_map: dict[tuple[str, str], dict] = {}
            
            for r in conn.execute(
                "SELECT source_type, source_handle, target_type, target_handle, anchor_text "
                "FROM internal_links"
            ).fetchall():
                src_key = (r["source_type"], r["source_handle"])
                tgt_key = (r["target_type"], r["target_handle"])
                
                if src_key not in degree_map:
                    degree_map[src_key] = {"inbound": 0, "outbound": 0}
                if tgt_key not in degree_map:
                    degree_map[tgt_key] = {"inbound": 0, "outbound": 0}
                
                degree_map[src_key]["outbound"] += 1
                degree_map[tgt_key]["inbound"] += 1
            
            # Sort by total degree and take top N
            sorted_nodes = sorted(
                degree_map.items(),
                key=lambda x: x[1]["inbound"] + x[1]["outbound"],
                reverse=True
            )[:max_nodes]
            
            included_keys = {k for k, _ in sorted_nodes}
            
            for (obj_type, handle), counts in sorted_nodes:
                nodes[(obj_type, handle)] = {
                    "id": f"{obj_type}:{handle}",
                    "object_type": obj_type,
                    "handle": handle,
                    "inbound": counts["inbound"],
                    "outbound": counts["outbound"],
                    "is_focus": False,
                }
            
            # Get edges between included nodes
            for r in conn.execute(
                "SELECT source_type, source_handle, target_type, target_handle, anchor_text "
                "FROM internal_links"
            ).fetchall():
                src_key = (r["source_type"], r["source_handle"])
                tgt_key = (r["target_type"], r["target_handle"])
                
                if src_key in included_keys and tgt_key in included_keys:
                    edges.append({
                        "source": f"{r['source_type']}:{r['source_handle']}",
                        "target": f"{r['target_type']}:{r['target_handle']}",
                        "anchor_text": r["anchor_text"],
                    })
        
        return success_response({
            "nodes": list(nodes.values()),
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        })
    finally:
        conn.close()


@router.get("/outcomes", response_model=SuccessResponse[dict])
def outcomes(days: int = Query(default=28, ge=1, le=365)):
    """Phase D: Get link suggestion outcomes/measurement data.
    
    Returns summary of applied/undo/dismiss counts and top targets.
    """
    import time
    
    conn = open_db_connection()
    try:
        cutoff = int(time.time()) - (days * 86400)
        
        # Get event counts by type
        event_counts = {}
        for r in conn.execute(
            "SELECT event_type, COUNT(*) AS c FROM link_suggestion_events "
            "WHERE created_at >= ? GROUP BY event_type",
            (cutoff,),
        ).fetchall():
            event_counts[r["event_type"]] = r["c"]
        
        applied = event_counts.get("apply", 0) + event_counts.get("auto_apply", 0)
        undone = event_counts.get("undo", 0)
        dismissed = event_counts.get("dismiss", 0)
        auto_applied = event_counts.get("auto_apply", 0)
        
        # Calculate undo rate
        undo_rate = (undone / applied * 100) if applied > 0 else 0
        
        # Get top targets (most linked to)
        top_targets = []
        for r in conn.execute(
            """
            SELECT target_type, target_handle, COUNT(*) AS c
            FROM link_suggestion_events
            WHERE event_type IN ('apply', 'auto_apply') AND created_at >= ?
            GROUP BY target_type, target_handle
            ORDER BY c DESC
            LIMIT 10
            """,
            (cutoff,),
        ).fetchall():
            top_targets.append({
                "target_type": r["target_type"],
                "target_handle": r["target_handle"],
                "count": r["c"],
            })
        
        # Get GSC clicks comparison (before/after for applied links)
        # This is best-effort - we compare the snapshot at apply time with current
        clicks_delta = None
        try:
            comparison = conn.execute(
                """
                SELECT 
                    SUM(e.gsc_clicks_at_event) AS clicks_at_apply,
                    COUNT(*) AS link_count
                FROM link_suggestion_events e
                WHERE e.event_type IN ('apply', 'auto_apply') 
                  AND e.created_at >= ?
                  AND e.gsc_clicks_at_event IS NOT NULL
                """,
                (cutoff,),
            ).fetchone()
            if comparison and comparison["link_count"] > 0:
                # Get current clicks for the same sources
                current_total = 0
                for r in conn.execute(
                    """
                    SELECT DISTINCT source_type, source_handle
                    FROM link_suggestion_events
                    WHERE event_type IN ('apply', 'auto_apply') AND created_at >= ?
                    """,
                    (cutoff,),
                ).fetchall():
                    src_type = r["source_type"]
                    src_handle = r["source_handle"]
                    if src_type == "blog_article":
                        blog_h, _, article_h = src_handle.partition("/")
                        row = conn.execute(
                            "SELECT COALESCE(gsc_clicks, 0) AS c FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                            (blog_h, article_h),
                        ).fetchone()
                    elif src_type in ("product", "collection", "page"):
                        table = {"product": "products", "collection": "collections", "page": "pages"}[src_type]
                        row = conn.execute(f"SELECT COALESCE(gsc_clicks, 0) AS c FROM {table} WHERE handle = ?", (src_handle,)).fetchone()
                    else:
                        row = None
                    if row:
                        current_total += row["c"]
                
                clicks_at_apply = comparison["clicks_at_apply"] or 0
                clicks_delta = {
                    "at_apply": clicks_at_apply,
                    "current": current_total,
                    "change": current_total - clicks_at_apply,
                    "link_count": comparison["link_count"],
                }
        except Exception:
            logger.warning("Failed to compute clicks delta", exc_info=True)
        
        return success_response({
            "days": days,
            "applied": applied,
            "auto_applied": auto_applied,
            "undone": undone,
            "dismissed": dismissed,
            "undo_rate_pct": round(undo_rate, 1),
            "top_targets": top_targets,
            "clicks_comparison": clicks_delta,
        })
    finally:
        conn.close()


@router.get("/settings", response_model=SuccessResponse[dict])
def get_internal_link_settings():
    """Get all internal link settings (thresholds, auto-apply config)."""
    conn = open_db_connection()
    try:
        from shopifyseo.dashboard_google import get_service_setting
        from shopifyseo.internal_links.auto_apply import (
            get_auto_apply_settings as _get_settings,
            get_auto_applied_today_count,
            DEFAULT_AUTO_APPLY_MIN_SCORE,
            DEFAULT_AUTO_APPLY_MAX_PER_DAY,
        )
        from shopifyseo.internal_links.pipeline import DEFAULT_SIM_THRESHOLD
        
        # Get similarity threshold
        sim_raw = get_service_setting(conn, "internal_link_sim_threshold", "")
        try:
            sim_threshold = float(sim_raw) if sim_raw else DEFAULT_SIM_THRESHOLD
        except ValueError:
            sim_threshold = DEFAULT_SIM_THRESHOLD
        
        # Get AI body links setting
        ai_body_raw = get_service_setting(conn, "internal_link_ai_body_links_enabled", "")
        ai_body_enabled = ai_body_raw.lower() in ("1", "true", "yes") if ai_body_raw else False
        
        # Get auto-apply settings
        auto_apply = _get_settings(conn)
        auto_apply["applied_today"] = get_auto_applied_today_count(conn)
        
        return success_response({
            "sim_threshold": sim_threshold,
            "sim_threshold_default": DEFAULT_SIM_THRESHOLD,
            "ai_body_links_enabled": ai_body_enabled,
            "ai_woven_enabled_types": ai_enabled_types(conn),
            "auto_apply_enabled": auto_apply["enabled"],
            "auto_apply_min_score": auto_apply["min_score"],
            "auto_apply_min_score_default": DEFAULT_AUTO_APPLY_MIN_SCORE,
            "auto_apply_max_per_day": auto_apply["max_per_day"],
            "auto_apply_max_per_day_default": DEFAULT_AUTO_APPLY_MAX_PER_DAY,
            "auto_apply_kinds": auto_apply["kinds"],
            "auto_applied_today": auto_apply["applied_today"],
        })
    finally:
        conn.close()


@router.put("/settings", response_model=SuccessResponse[dict])
def save_internal_link_settings(
    sim_threshold: float | None = Query(default=None, ge=0.1, le=1.0),
    ai_body_links_enabled: bool | None = Query(default=None),
    ai_woven_enabled_types: str | None = Query(default=None),
    auto_apply_enabled: bool | None = Query(default=None),
    auto_apply_min_score: float | None = Query(default=None, ge=0.1, le=2.0),
    auto_apply_max_per_day: int | None = Query(default=None, ge=1, le=1000),
):
    """Save internal link settings. Only non-null parameters are updated."""
    conn = open_db_connection()
    try:
        from shopifyseo.dashboard_google import set_service_setting
        
        saved: dict[str, str | float | int | bool] = {}
        if ai_woven_enabled_types is not None:
            enabled = [t for t in ai_woven_enabled_types.split(",") if t]
            if any(t not in SOURCE_TYPES for t in enabled):
                raise HTTPException(status_code=400, detail="Unsupported AI link source type.")
            set_service_setting(conn, AI_TYPES_KEY, ",".join(sorted(set(enabled))))
            saved["ai_woven_enabled_types"] = ",".join(sorted(set(enabled)))
        
        if sim_threshold is not None:
            set_service_setting(conn, "internal_link_sim_threshold", str(sim_threshold))
            saved["sim_threshold"] = sim_threshold
        
        if ai_body_links_enabled is not None:
            set_service_setting(conn, "internal_link_ai_body_links_enabled", "1" if ai_body_links_enabled else "")
            saved["ai_body_links_enabled"] = ai_body_links_enabled
        
        if auto_apply_enabled is not None:
            set_service_setting(conn, "internal_link_auto_apply_enabled", "1" if auto_apply_enabled else "")
            saved["auto_apply_enabled"] = auto_apply_enabled
        
        if auto_apply_min_score is not None:
            set_service_setting(conn, "internal_link_auto_apply_min_score", str(auto_apply_min_score))
            saved["auto_apply_min_score"] = auto_apply_min_score
        
        if auto_apply_max_per_day is not None:
            set_service_setting(conn, "internal_link_auto_apply_max_per_day", str(auto_apply_max_per_day))
            saved["auto_apply_max_per_day"] = auto_apply_max_per_day
        
        conn.commit()
        return success_response({"saved": saved})
    finally:
        conn.close()


@router.get("/auto-apply/settings", response_model=SuccessResponse[dict])
def get_auto_apply_settings():
    """Phase E: Get auto-apply settings (legacy endpoint, use /settings instead)."""
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.auto_apply import (
            get_auto_apply_settings as _get_settings,
            get_auto_applied_today_count,
        )
        
        settings = _get_settings(conn)
        settings["applied_today"] = get_auto_applied_today_count(conn)
        return success_response(settings)
    finally:
        conn.close()


@router.post("/auto-apply/run", response_model=SuccessResponse[dict])
def run_auto_apply(dry_run: bool = Query(default=False)):
    """Phase E: Manually trigger auto-apply (respects settings and quota)."""
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.auto_apply import run_auto_apply as _run
        
        result = _run(conn, base_url=_base_url(conn), dry_run=dry_run)
        return success_response(result)
    except LinkConflict as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": {"code": "link_conflict", **exc.detail}})
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Auto-apply failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()
