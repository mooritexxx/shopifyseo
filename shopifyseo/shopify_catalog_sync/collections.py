import sqlite3
from pathlib import Path

from .db import (
    now_iso,
    json_dumps,
    open_db,
    start_run,
    finish_run,
    fetch_all_collections,
    fetch_collection_by_id,
    fetch_collection_products,
)


def replace_collection_children(conn: sqlite3.Connection, table: str, collection_id: str) -> None:
    conn.execute(f"DELETE FROM {table} WHERE collection_shopify_id = ?", (collection_id,))


def upsert_collection(conn: sqlite3.Connection, collection: dict, synced_at: str) -> int:
    image_payload = collection.get("image")
    image_json_val = json_dumps(image_payload) if image_payload else None
    conn.execute(
        """
        INSERT INTO collections (
          shopify_id,
          title,
          handle,
          updated_at,
          description_html,
          seo_title,
          seo_description,
          rule_set_json,
          image_json,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
          title = excluded.title,
          handle = excluded.handle,
          updated_at = excluded.updated_at,
          description_html = excluded.description_html,
          seo_title = excluded.seo_title,
          seo_description = excluded.seo_description,
          rule_set_json = excluded.rule_set_json,
          image_json = excluded.image_json,
          raw_json = excluded.raw_json,
          synced_at = excluded.synced_at
        """,
        (
            collection["id"],
            collection["title"],
            collection["handle"],
            collection.get("updatedAt") or "",
            collection.get("descriptionHtml") or "",
            (collection.get("seo") or {}).get("title") or "",
            (collection.get("seo") or {}).get("description") or "",
            json_dumps(collection.get("ruleSet")) if collection.get("ruleSet") else None,
            image_json_val,
            json_dumps(collection),
            synced_at,
        ),
    )

    replace_collection_children(conn, "collection_metafields", collection["id"])
    metafields = [edge["node"] for edge in (collection.get("metafields") or {}).get("edges", [])]
    conn.executemany(
        """
        INSERT INTO collection_metafields (
          shopify_id,
          collection_shopify_id,
          namespace,
          key,
          type,
          value,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                metafield["id"],
                collection["id"],
                metafield["namespace"],
                metafield["key"],
                metafield.get("type") or "",
                metafield.get("value") or "",
                json_dumps(metafield),
                synced_at,
            )
            for metafield in metafields
        ],
    )
    return len(metafields)


def _mark_api_unreachable_collections(conn: sqlite3.Connection, synced_ids: set[str]) -> int:
    """Mark local collections as api_unreachable if not returned by Admin API.

    Collections missing from Admin API may be:
    - Actually deleted from Shopify (staff, app, etc.)
    - API-invisible due to broken smart-collection rules
    - API-invisible due to metafield-only rule configurations

    We mark them as api_unreachable rather than deleting them, preserving local SEO data.
    Pending link suggestions involving unreachable collections are also dismissed.

    Also clears api_unreachable for collections that ARE now visible via API.

    Returns the count of collections newly marked as unreachable.
    """
    # Get all local collections with shopify_id
    local_rows = conn.execute(
        "SELECT shopify_id, handle FROM collections WHERE shopify_id IS NOT NULL"
    ).fetchall()
    local_ids = {r["shopify_id"] for r in local_rows}
    handle_by_id = {r["shopify_id"]: r["handle"] for r in local_rows}

    # Collections in local DB but not in API response -> mark as unreachable
    unreachable_ids = local_ids - synced_ids
    newly_unreachable = 0
    if unreachable_ids:
        # Only count those that weren't already marked
        cur = conn.execute(
            f"SELECT COUNT(*) FROM collections WHERE shopify_id IN ({','.join('?' for _ in unreachable_ids)}) "
            "AND (api_unreachable IS NULL OR api_unreachable = 0)",
            list(unreachable_ids),
        )
        newly_unreachable = cur.fetchone()[0]

        conn.executemany(
            "UPDATE collections SET api_unreachable = 1 WHERE shopify_id = ?",
            [(cid,) for cid in unreachable_ids],
        )

        # Dismiss pending link suggestions that reference unreachable collections
        # (as source or target - both are now invalid for Apply)
        unreachable_handles = [handle_by_id[cid] for cid in unreachable_ids if cid in handle_by_id]
        if unreachable_handles:
            try:
                conn.executemany(
                    "UPDATE link_suggestions SET status = 'dismissed' "
                    "WHERE status = 'suggested' AND source_type = 'collection' AND source_handle = ?",
                    [(h,) for h in unreachable_handles],
                )
                conn.executemany(
                    "UPDATE link_suggestions SET status = 'dismissed' "
                    "WHERE status = 'suggested' AND target_type = 'collection' AND target_handle = ?",
                    [(h,) for h in unreachable_handles],
                )
            except Exception:
                pass  # link_suggestions table may not exist in all contexts

    # Collections that ARE in API response -> clear unreachable flag if set
    reachable_ids = local_ids & synced_ids
    if reachable_ids:
        conn.executemany(
            "UPDATE collections SET api_unreachable = 0 WHERE shopify_id = ? AND api_unreachable = 1",
            [(cid,) for cid in reachable_ids],
        )

    return newly_unreachable


def sync_collections(
    db_path: Path,
    page_size: int,
    progress_callback=None,
    *,
    collections: list[dict] | None = None,
    queue_scope: str | None = None,
) -> dict:
    run_conn = open_db(db_path)
    run_id = start_run(run_conn)
    run_conn.close()
    synced_at = now_iso()
    conn: sqlite3.Connection | None = None
    try:
        if collections is None:
            collections = fetch_all_collections(page_size)
        else:
            collections = list(collections)
        if progress_callback is not None:
            progress_callback("collections", 0, len(collections))
        products_by_collection_id: dict[str, list[dict]] = {}
        for collection in collections:
            products_by_collection_id[collection["id"]] = fetch_collection_products(collection["id"], 250)

        collection_count = 0
        metafield_count = 0
        membership_count = 0

        if queue_scope:
            from shopifyseo.dashboard_actions import _sync_queue as _sq

            _sq.sync_queue_seed(
                queue_scope,
                [
                    ("collection", str(c.get("id") or "").strip(), (c.get("handle") or "")[:200])
                    for c in collections
                    if str(c.get("id") or "").strip()
                ],
            )

        conn = open_db(db_path)

        # Track synced IDs to prune deleted collections later
        synced_ids: set[str] = set()

        for collection in collections:
            cid = str(collection.get("id") or "").strip()
            rk = (
                _sq.catalog_sync_row_key("collection", cid, (collection.get("handle") or "")[:200])
                if queue_scope and cid
                else ""
            )
            if queue_scope and cid:
                _sq.sync_queue_mark_running(queue_scope, rk)
            ok = True
            err_msg: str | None = None
            try:
                metafield_count += upsert_collection(conn, collection, synced_at)
                replace_collection_children(conn, "collection_products", collection["id"])
                products = products_by_collection_id.get(collection["id"], [])
                conn.executemany(
                    """
                    INSERT INTO collection_products (
                      collection_shopify_id,
                      product_shopify_id,
                      product_handle,
                      product_title,
                      synced_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            collection["id"],
                            product["id"],
                            product.get("handle") or "",
                            product.get("title") or "",
                            synced_at,
                        )
                        for product in products
                    ],
                )
                membership_count += len(products)
                collection_count += 1
                synced_ids.add(collection["id"])
            except Exception as exc:
                ok = False
                err_msg = str(exc)
                raise
            finally:
                if queue_scope and cid:
                    _sq.sync_queue_mark_done(queue_scope, rk, ok, err_msg, pop_completed=ok)
            if progress_callback is not None:
                progress_callback("collections", collection_count, len(collections))

        # Mark collections missing from API as api_unreachable (do NOT delete them)
        unreachable_count = _mark_api_unreachable_collections(conn, synced_ids)

        conn.commit()
        finish_run(
            conn,
            run_id,
            status="success",
            collections_synced=collection_count,
            collection_metafields_synced=metafield_count,
            collection_products_synced=membership_count,
        )
        return {
            "db_path": str(db_path),
            "collections_synced": collection_count,
            "collections_api_unreachable": unreachable_count,
            "collection_metafields_synced": metafield_count,
            "collection_products_synced": membership_count,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        if conn is None:
            conn = open_db(db_path)
        else:
            conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        if conn is not None:
            conn.close()


class ShopifyCollectionUnreachable(RuntimeError):
    """Raised when a collection cannot be accessed via Admin API.

    The collection may still exist in Shopify Admin UI and on the storefront,
    but the Admin GraphQL/REST APIs cannot read it. This typically affects
    smart collections with certain metafield-only rule configurations.
    """

    def __init__(self, collection_id: str, handle: str | None = None):
        self.collection_id = collection_id
        self.handle = handle
        handle_part = f" (handle={handle})" if handle else ""
        super().__init__(
            f"Collection not accessible via Admin API: {collection_id}{handle_part}. "
            "The collection may still exist in Shopify Admin but is not returned by GraphQL/REST APIs."
        )


def mark_collection_api_unreachable(conn: sqlite3.Connection, collection_id: str) -> str | None:
    """Mark a collection as api_unreachable (Admin API cannot access it). Returns handle if found."""
    row = conn.execute(
        "SELECT handle FROM collections WHERE shopify_id = ?", (collection_id,)
    ).fetchone()
    if row:
        handle = row["handle"]
        conn.execute("UPDATE collections SET api_unreachable = 1 WHERE shopify_id = ?", (collection_id,))
        conn.commit()
        return handle
    return None


def sync_collection(db_path: Path, collection_id: str, page_size: int = 250) -> dict:
    run_conn = open_db(db_path)
    run_id = start_run(run_conn)
    run_conn.close()
    synced_at = now_iso()
    conn: sqlite3.Connection | None = None
    try:
        collection = fetch_collection_by_id(collection_id)
        if not collection:
            # Collection is not accessible via Admin API - mark as unreachable but do NOT delete
            conn = open_db(db_path)
            handle = mark_collection_api_unreachable(conn, collection_id)
            finish_run(conn, run_id, status="api_unreachable", error_message=f"Collection not accessible via API: {collection_id}")
            raise ShopifyCollectionUnreachable(collection_id, handle)
        products = fetch_collection_products(collection["id"], page_size)

        conn = open_db(db_path)
        metafield_count = upsert_collection(conn, collection, synced_at)
        replace_collection_children(conn, "collection_products", collection["id"])
        conn.executemany(
            """
            INSERT INTO collection_products (
              collection_shopify_id,
              product_shopify_id,
              product_handle,
              product_title,
              synced_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    collection["id"],
                    product["id"],
                    product.get("handle") or "",
                    product.get("title") or "",
                    synced_at,
                )
                for product in products
            ],
        )
        conn.commit()
        finish_run(
            conn,
            run_id,
            status="success",
            collections_synced=1,
            collection_metafields_synced=metafield_count,
            collection_products_synced=len(products),
        )
        return {
            "db_path": str(db_path),
            "collections_synced": 1,
            "collection_metafields_synced": metafield_count,
            "collection_products_synced": len(products),
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except ShopifyCollectionUnreachable:
        raise
    except Exception as exc:
        if conn is None:
            conn = open_db(db_path)
        else:
            conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        if conn is not None:
            conn.close()
