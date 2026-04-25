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
            except Exception as exc:
                ok = False
                err_msg = str(exc)
                raise
            finally:
                if queue_scope and cid:
                    _sq.sync_queue_mark_done(queue_scope, rk, ok, err_msg, pop_completed=ok)
            if progress_callback is not None:
                progress_callback("collections", collection_count, len(collections))
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


def sync_collection(db_path: Path, collection_id: str, page_size: int = 250) -> dict:
    run_conn = open_db(db_path)
    run_id = start_run(run_conn)
    run_conn.close()
    synced_at = now_iso()
    conn: sqlite3.Connection | None = None
    try:
        collection = fetch_collection_by_id(collection_id)
        if not collection:
            raise RuntimeError(f"Collection not found in Shopify: {collection_id}")
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
