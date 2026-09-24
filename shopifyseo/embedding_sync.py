"""Event-driven embedding sync helper.

Provides `enqueue_embedding_sync` for triggering embedding updates after mutations
without blocking the primary write path. Runs in daemon background threads and is
idempotent/safe to call multiple times.

Usage:
    from shopifyseo.embedding_sync import enqueue_embedding_sync

    # After saving a product:
    enqueue_embedding_sync(db_path, object_types=["product"], handles=["my-product"])

    # After bulk keyword update:
    enqueue_embedding_sync(db_path, object_types=["keyword"])

    # After full catalog sync:
    enqueue_embedding_sync(db_path, object_types=["product", "collection", "page", "blog_article"])
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# Types that support single-handle sync via sync_embedding_for_handle
_SINGLE_HANDLE_TYPES = frozenset({
    "product", "collection", "page", "blog_article",
    "cluster", "article_idea",
})

# All embeddable types (from embedding_store.EMBEDDABLE_TYPES)
EMBEDDABLE_TYPES = (
    "product", "collection", "page", "blog_article",
    "cluster", "gsc_queries", "keyword", "article_idea", "competitor_page",
)


def _open_db(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection for background embedding work."""
    from .shopify_catalog_sync.db import open_db
    return open_db(db_path if isinstance(db_path, Path) else Path(db_path))


def _sync_single_handle(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
) -> dict:
    """Sync embedding for a single object. Returns result dict."""
    from .embedding_store import sync_embedding_for_handle
    return sync_embedding_for_handle(conn, object_type, handle)


def _sync_type(conn: sqlite3.Connection, object_type: str) -> dict:
    """Sync all embeddings for a type. Returns result dict."""
    from .embedding_store import sync_embeddings
    return sync_embeddings(conn, object_type=object_type)


def _run_embedding_sync(
    db_path: str | Path,
    object_types: Sequence[str],
    handles: Sequence[str] | None,
) -> None:
    """Background worker that performs the embedding sync."""
    conn: sqlite3.Connection | None = None
    try:
        conn = _open_db(db_path)

        if handles and len(handles) == len(object_types):
            # Single-handle mode: sync specific objects
            for obj_type, handle in zip(object_types, handles):
                if obj_type in _SINGLE_HANDLE_TYPES:
                    try:
                        result = _sync_single_handle(conn, obj_type, handle)
                        if result.get("error"):
                            logger.warning(
                                "Single-handle embedding sync error for %s:%s — %s",
                                obj_type, handle, result.get("error"),
                            )
                        else:
                            logger.debug(
                                "Embedded %s:%s — embedded=%d, skipped=%d",
                                obj_type, handle,
                                result.get("embedded", 0), result.get("skipped", 0),
                            )
                    except Exception:
                        logger.warning(
                            "Single-handle embedding sync failed for %s:%s",
                            obj_type, handle, exc_info=True,
                        )
                else:
                    # Fall back to type-scoped sync for unsupported types
                    try:
                        _sync_type(conn, obj_type)
                    except Exception:
                        logger.warning(
                            "Type-scoped embedding sync failed for %s (fallback from single-handle)",
                            obj_type, exc_info=True,
                        )
        else:
            # Type-scoped mode: sync all objects of specified types
            for obj_type in object_types:
                try:
                    result = _sync_type(conn, obj_type)
                    if result.get("skipped") is True:
                        logger.debug(
                            "Embedding sync for %s skipped (already running)",
                            obj_type,
                        )
                    else:
                        logger.debug(
                            "Embedding sync for %s complete — embedded=%d, skipped=%d, pruned=%d",
                            obj_type,
                            result.get("embedded", 0),
                            result.get("skipped", 0),
                            result.get("pruned", 0),
                        )
                except Exception:
                    logger.warning(
                        "Type-scoped embedding sync failed for %s",
                        obj_type, exc_info=True,
                    )
    except Exception:
        logger.warning("Background embedding sync failed", exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def enqueue_embedding_sync(
    db_path: str | Path,
    *,
    object_types: Sequence[str] | None = None,
    handles: Sequence[str] | None = None,
) -> None:
    """Enqueue embedding sync to run in a background daemon thread.

    This function returns immediately without blocking. The embedding work
    runs asynchronously and logs any failures without raising.

    Args:
        db_path: Path to the SQLite database.
        object_types: List of object types to sync (e.g. ["product", "keyword"]).
                      If None, syncs all embeddable types.
        handles: Optional list of specific handles to sync. When provided with
                 matching object_types (same length), uses single-handle sync
                 for supported types instead of full type-scoped sync.
                 For blog_article, use "{blog_handle}/{article_handle}" format.

    Examples:
        # Sync all embeddings for all types
        enqueue_embedding_sync(db_path)

        # Sync just keywords after bulk update
        enqueue_embedding_sync(db_path, object_types=["keyword"])

        # Sync a single product after update
        enqueue_embedding_sync(db_path, object_types=["product"], handles=["my-product"])

        # Sync multiple specific objects
        enqueue_embedding_sync(
            db_path,
            object_types=["product", "collection"],
            handles=["product-1", "collection-1"],
        )
    """
    if object_types is None:
        types_to_sync = list(EMBEDDABLE_TYPES)
    else:
        # Validate and filter to known types
        types_to_sync = [t for t in object_types if t in EMBEDDABLE_TYPES]
        if not types_to_sync:
            logger.warning(
                "enqueue_embedding_sync called with no valid object_types: %s",
                object_types,
            )
            return

    # Validate handles if provided
    if handles is not None:
        if len(handles) != len(types_to_sync):
            logger.warning(
                "enqueue_embedding_sync: handles length (%d) != object_types length (%d), "
                "falling back to type-scoped sync",
                len(handles), len(types_to_sync),
            )
            handles = None

    thread = threading.Thread(
        target=_run_embedding_sync,
        args=(db_path, types_to_sync, handles),
        daemon=True,
    )
    thread.start()


def enqueue_embedding_sync_for_type(
    db_path: str | Path,
    object_type: str,
) -> None:
    """Convenience wrapper to sync all objects of a single type."""
    enqueue_embedding_sync(db_path, object_types=[object_type])


def enqueue_embedding_sync_for_handle(
    db_path: str | Path,
    object_type: str,
    handle: str,
) -> None:
    """Convenience wrapper to sync a single object by handle."""
    enqueue_embedding_sync(db_path, object_types=[object_type], handles=[handle])


def _get_db_path_from_connection(conn: sqlite3.Connection) -> str | None:
    """Extract database file path from an open connection."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row and len(row) >= 3:
            return row[2]  # (seq, name, file)
    except Exception:
        pass
    return None


def enqueue_embedding_sync_from_conn(
    conn: sqlite3.Connection,
    *,
    object_types: Sequence[str] | None = None,
    handles: Sequence[str] | None = None,
) -> None:
    """Enqueue embedding sync using db_path extracted from an open connection.

    This is useful when you have a connection but not the db_path. Falls back
    to no-op with a warning if the path cannot be extracted.
    """
    db_path = _get_db_path_from_connection(conn)
    if not db_path:
        logger.warning(
            "Could not extract db_path from connection for embedding sync"
        )
        return
    enqueue_embedding_sync(db_path, object_types=object_types, handles=handles)
