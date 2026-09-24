"""API endpoints for the internal linking engine."""

import logging
import threading
from typing import Optional

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


@router.get("/suggestions", response_model=SuccessResponse[list])
def suggestions(
    source_type: str | None = Query(default=None),
    source_handle: str | None = Query(default=None),
    status_filter: str = Query(default="suggested", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.anchors import get_weak_anchor_warning
        
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
        rows = []
        for r in conn.execute(sql, params).fetchall():
            row_dict = dict(r)
            # Add weak anchor warning for phrase_wrap suggestions
            if row_dict.get("kind") == "phrase_wrap":
                row_dict["weak_anchor_warning"] = get_weak_anchor_warning(row_dict.get("anchor_phrase"))
            else:
                row_dict["weak_anchor_warning"] = None
            rows.append(row_dict)
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


@router.get("/applied", response_model=SuccessResponse[list])
def applied_list(
    source_type: str | None = Query(default=None),
    source_handle: str | None = Query(default=None),
    problems_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List applied suggestions with live-present status check."""
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
    """Undo an applied suggestion: remove the link from live Shopify body."""
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import undo_suggestion

        return success_response(undo_suggestion(conn, suggestion_id, base_url=_base_url(conn)))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Undo suggestion failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    finally:
        conn.close()


@router.get("/suggestions/{suggestion_id}/preview", response_model=SuccessResponse[dict])
def preview(suggestion_id: int):
    """Get a preview of what applying this suggestion would look like."""
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import _SOURCE_META, _load_source_row, wrap_phrase_in_html
        from shopifyseo.dashboard_queries._urls import object_url_with_base
        import re

        base_url = _base_url(conn)
        sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not sug:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found")

        row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
        body_col = _SOURCE_META[sug["source_type"]][2]
        current_body = row[body_col] or ""
        url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])

        preview_data = {
            "suggestion_id": suggestion_id,
            "kind": sug["kind"],
            "current_body_snippet": None,
            "preview_body_snippet": None,
            "anchor_phrase": sug["anchor_phrase"],
            "target_url": url,
        }

        if sug["kind"] == "phrase_wrap" and sug["anchor_phrase"]:
            # Find the sentence/context containing the anchor phrase
            phrase = sug["anchor_phrase"]
            # Extract a snippet around the phrase (up to 200 chars before and after)
            pattern = re.compile(
                r'([^.!?]*?' + re.escape(phrase) + r'[^.!?]*[.!?]?)',
                re.IGNORECASE | re.DOTALL
            )
            match = pattern.search(current_body)
            if match:
                snippet = match.group(0).strip()
                # Clean up HTML tags for display
                clean_snippet = re.sub(r'<[^>]+>', ' ', snippet)
                clean_snippet = ' '.join(clean_snippet.split())[:300]
                preview_data["current_body_snippet"] = clean_snippet
                
                # Show preview with link
                new_body = wrap_phrase_in_html(current_body, phrase, url)
                if new_body:
                    match_new = pattern.search(new_body)
                    if match_new:
                        new_snippet = match_new.group(0).strip()
                        # Show the HTML with the link for preview
                        preview_data["preview_body_snippet"] = new_snippet[:400]
        
        elif sug["kind"] == "ai_woven" and sug["ai_anchor_html"]:
            # For ai_woven, show the diff between current and generated
            ai_html = sug["ai_anchor_html"]
            # Find the paragraph containing the new link
            link_match = re.search(
                r'<p>[^<]*<a\s+href=["\']' + re.escape(url) + r'["\'][^>]*>[^<]*</a>[^<]*</p>',
                ai_html,
                re.IGNORECASE | re.DOTALL
            )
            if link_match:
                preview_data["preview_body_snippet"] = link_match.group(0)[:400]
            else:
                # Just show first 300 chars of ai_anchor_html
                preview_data["preview_body_snippet"] = ai_html[:400] + ("..." if len(ai_html) > 400 else "")
            
            # Show corresponding current body snippet
            preview_data["current_body_snippet"] = current_body[:300] + ("..." if len(current_body) > 300 else "")

        return success_response(preview_data)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Preview generation failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
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
