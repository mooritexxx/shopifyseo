import sqlite3
from pathlib import Path

from .db import (
    now_iso,
    json_dumps,
    metafield_value,
    metafield_reference_json,
    parse_json_list,
    fetch_metaobjects_by_ids,
    upsert_metaobjects,
    resolve_product_metaobject_labels,
    open_db,
    start_run,
    finish_run,
    fetch_all_products,
    fetch_product_by_id,
)


def replace_children(conn: sqlite3.Connection, table: str, product_id: str) -> None:
    conn.execute(f"DELETE FROM {table} WHERE product_shopify_id = ?", (product_id,))


def _product_images_for_upsert(product: dict) -> list[dict]:
    """Normalize gallery rows from Admin API.

    Shopify storefronts use ``Product.media`` (MediaImage). The legacy ``Product.images``
    connection often returns only a single image; prefer media and fall back to images.
    Each returned dict matches the legacy ``images`` node shape: id, altText, url, width, height.
    """
    rows: list[dict] = []
    for edge in (product.get("media") or {}).get("edges", []):
        node = (edge or {}).get("node") or {}
        nested = node.get("image")
        if not isinstance(nested, dict):
            continue
        url = (nested.get("url") or "").strip()
        gid = (node.get("id") or "").strip()
        if not url or not gid:
            continue
        rows.append(
            {
                "id": gid,
                "altText": node.get("alt") or "",
                "url": url,
                "width": nested.get("width"),
                "height": nested.get("height"),
            }
        )
    if rows:
        return rows
    return [edge["node"] for edge in (product.get("images") or {}).get("edges", [])]


def prune_deleted_products(conn: sqlite3.Connection, live_products: list[dict]) -> int:
    live_ids = {product["id"] for product in live_products}
    stale_rows = conn.execute(
        "SELECT shopify_id, handle FROM products"
    ).fetchall()
    stale = [row for row in stale_rows if row[0] not in live_ids]
    for shopify_id, handle in stale:
        conn.execute(
            "DELETE FROM seo_recommendations WHERE object_type = 'product' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM gsc_query_rows WHERE object_type = 'product' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM gsc_query_dimension_rows WHERE object_type = 'product' AND object_handle = ?",
            (handle,),
        )
        conn.execute(
            "DELETE FROM google_api_cache WHERE object_type = 'product' AND object_handle = ?",
            (handle,),
        )
        conn.execute("DELETE FROM products WHERE shopify_id = ?", (shopify_id,))
    return len(stale)


def upsert_product(conn: sqlite3.Connection, product: dict, synced_at: str) -> tuple[int, int, int]:
    battery_size = metafield_value(product, "custom", "battery_size")
    charging_port = metafield_value(product, "custom", "charging_port")
    coil = metafield_value(product, "custom", "coil")
    custom_collection = metafield_value(product, "custom", "collection")
    device_type = metafield_value(product, "custom", "device_type")
    nicotine_strength = metafield_value(product, "custom", "nicotine_strength")
    puff_count = metafield_value(product, "custom", "puff_count")
    size = metafield_value(product, "custom", "size")
    battery_type_refs_json = metafield_reference_json(product, "shopify", "battery-type")
    coil_connection_refs_json = metafield_reference_json(product, "shopify", "coil-connection")
    color_pattern_refs_json = metafield_reference_json(product, "shopify", "color-pattern")
    vaporizer_style_refs_json = metafield_reference_json(product, "shopify", "e-cigarette-vaporizer-style")
    e_liquid_flavor_refs_json = metafield_reference_json(product, "shopify", "e-liquid-flavor")
    vaping_style_refs_json = metafield_reference_json(product, "shopify", "vaping-style")
    conn.execute(
        """
        INSERT INTO products (
          shopify_id,
          legacy_resource_id,
          title,
          handle,
          vendor,
          product_type,
          status,
          created_at,
          updated_at,
          published_at,
          description_html,
          tags_json,
          seo_title,
          seo_description,
          total_inventory,
          tracks_inventory,
          category_full_name,
          battery_size,
          charging_port,
          coil,
          custom_collection,
          device_type,
          nicotine_strength,
          puff_count,
          size,
          battery_type_refs_json,
          coil_connection_refs_json,
          color_pattern_refs_json,
          vaporizer_style_refs_json,
          e_liquid_flavor_refs_json,
          vaping_style_refs_json,
          battery_type_labels_json,
          coil_connection_labels_json,
          color_pattern_labels_json,
          vaporizer_style_labels_json,
          e_liquid_flavor_labels_json,
          vaping_style_labels_json,
          online_store_url,
          options_json,
          featured_image_json,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
          legacy_resource_id = excluded.legacy_resource_id,
          title = excluded.title,
          handle = excluded.handle,
          vendor = excluded.vendor,
          product_type = excluded.product_type,
          status = excluded.status,
          created_at = excluded.created_at,
          updated_at = excluded.updated_at,
          published_at = excluded.published_at,
          description_html = excluded.description_html,
          tags_json = excluded.tags_json,
          seo_title = excluded.seo_title,
          seo_description = excluded.seo_description,
          total_inventory = excluded.total_inventory,
          tracks_inventory = excluded.tracks_inventory,
          category_full_name = excluded.category_full_name,
          battery_size = excluded.battery_size,
          charging_port = excluded.charging_port,
          coil = excluded.coil,
          custom_collection = excluded.custom_collection,
          device_type = excluded.device_type,
          nicotine_strength = excluded.nicotine_strength,
          puff_count = excluded.puff_count,
          size = excluded.size,
          battery_type_refs_json = excluded.battery_type_refs_json,
          coil_connection_refs_json = excluded.coil_connection_refs_json,
          color_pattern_refs_json = excluded.color_pattern_refs_json,
          vaporizer_style_refs_json = excluded.vaporizer_style_refs_json,
          e_liquid_flavor_refs_json = excluded.e_liquid_flavor_refs_json,
          vaping_style_refs_json = excluded.vaping_style_refs_json,
          battery_type_labels_json = excluded.battery_type_labels_json,
          coil_connection_labels_json = excluded.coil_connection_labels_json,
          color_pattern_labels_json = excluded.color_pattern_labels_json,
          vaporizer_style_labels_json = excluded.vaporizer_style_labels_json,
          e_liquid_flavor_labels_json = excluded.e_liquid_flavor_labels_json,
          vaping_style_labels_json = excluded.vaping_style_labels_json,
          online_store_url = excluded.online_store_url,
          options_json = excluded.options_json,
          featured_image_json = excluded.featured_image_json,
          raw_json = excluded.raw_json,
          synced_at = excluded.synced_at
        """,
        (
            product["id"],
            str(product.get("legacyResourceId") or ""),
            product["title"],
            product["handle"],
            product.get("vendor") or "",
            product.get("productType") or "",
            product.get("status") or "",
            product.get("createdAt") or "",
            product.get("updatedAt") or "",
            product.get("publishedAt") or "",
            product.get("descriptionHtml") or "",
            json_dumps(product.get("tags") or []),
            (product.get("seo") or {}).get("title") or "",
            (product.get("seo") or {}).get("description") or "",
            product.get("totalInventory"),
            1 if product.get("tracksInventory") else 0,
            ((product.get("category") or {}).get("fullName")) or "",
            battery_size,
            charging_port,
            coil,
            custom_collection,
            device_type,
            nicotine_strength,
            puff_count,
            size,
            battery_type_refs_json,
            coil_connection_refs_json,
            color_pattern_refs_json,
            vaporizer_style_refs_json,
            e_liquid_flavor_refs_json,
            vaping_style_refs_json,
            "",
            "",
            "",
            "",
            "",
            "",
            product.get("onlineStoreUrl") or "",
            json_dumps(product.get("options") or []),
            json_dumps(product.get("featuredImage")) if product.get("featuredImage") else None,
            json_dumps(product),
            synced_at,
        ),
    )

    replace_children(conn, "product_variants", product["id"])
    replace_children(conn, "product_images", product["id"])
    replace_children(conn, "product_metafields", product["id"])

    variants = [edge["node"] for edge in (product.get("variants") or {}).get("edges", [])]
    conn.executemany(
        """
        INSERT INTO product_variants (
          shopify_id,
          product_shopify_id,
          legacy_resource_id,
          title,
          sku,
          barcode,
          price,
          compare_at_price,
          position,
          inventory_policy,
          inventory_quantity,
          taxable,
          selected_options_json,
          image_json,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                variant["id"],
                product["id"],
                str(variant.get("legacyResourceId") or ""),
                variant["title"],
                variant.get("sku") or "",
                variant.get("barcode") or "",
                variant.get("price") or "",
                variant.get("compareAtPrice") or "",
                variant.get("position"),
                variant.get("inventoryPolicy") or "",
                variant.get("inventoryQuantity"),
                1 if variant.get("taxable") else 0,
                json_dumps(variant.get("selectedOptions") or []),
                json_dumps(variant.get("image")) if variant.get("image") else None,
                json_dumps(variant),
                synced_at,
            )
            for variant in variants
        ],
    )

    images = _product_images_for_upsert(product)
    conn.executemany(
        """
        INSERT INTO product_images (
          shopify_id,
          product_shopify_id,
          position,
          alt_text,
          url,
          width,
          height,
          raw_json,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                image["id"],
                product["id"],
                pos,
                image.get("altText") or "",
                image["url"],
                image.get("width"),
                image.get("height"),
                json_dumps(image),
                synced_at,
            )
            for pos, image in enumerate(images, start=1)
        ],
    )

    metafields = [edge["node"] for edge in (product.get("metafields") or {}).get("edges", [])]
    conn.executemany(
        """
        INSERT INTO product_metafields (
          shopify_id,
          product_shopify_id,
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
                product["id"],
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

    return len(variants), len(images), len(metafields)


def sync_products(db_path: Path, page_size: int, progress_callback=None) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        def _on_product_page_loaded(n_so_far: int) -> None:
            if progress_callback is not None:
                progress_callback("products", 0, n_so_far)

        products = fetch_all_products(page_size, after_page=_on_product_page_loaded)
        all_metaobject_ids = sorted(
            {
                metaobject_id
                for product in products
                for refs_column in [
                    metafield_reference_json(product, "shopify", "battery-type"),
                    metafield_reference_json(product, "shopify", "coil-connection"),
                    metafield_reference_json(product, "shopify", "color-pattern"),
                    metafield_reference_json(product, "shopify", "e-cigarette-vaporizer-style"),
                    metafield_reference_json(product, "shopify", "e-liquid-flavor"),
                    metafield_reference_json(product, "shopify", "vaping-style"),
                ]
                for metaobject_id in parse_json_list(refs_column)
            }
        )
        for start in range(0, len(all_metaobject_ids), 100):
            batch_ids = all_metaobject_ids[start:start + 100]
            upsert_metaobjects(conn, fetch_metaobjects_by_ids(batch_ids), synced_at)
        if progress_callback is not None:
            progress_callback("products", 0, len(products))
        product_count = 0
        variant_count = 0
        image_count = 0
        metafield_count = 0

        for product in products:
            v_count, i_count, m_count = upsert_product(conn, product, synced_at)
            product_count += 1
            variant_count += v_count
            image_count += i_count
            metafield_count += m_count
            resolve_product_metaobject_labels(
                conn,
                product["id"],
                {
                    "battery_type_refs_json": metafield_reference_json(product, "shopify", "battery-type"),
                    "coil_connection_refs_json": metafield_reference_json(product, "shopify", "coil-connection"),
                    "color_pattern_refs_json": metafield_reference_json(product, "shopify", "color-pattern"),
                    "vaporizer_style_refs_json": metafield_reference_json(product, "shopify", "e-cigarette-vaporizer-style"),
                    "e_liquid_flavor_refs_json": metafield_reference_json(product, "shopify", "e-liquid-flavor"),
                    "vaping_style_refs_json": metafield_reference_json(product, "shopify", "vaping-style"),
                },
            )
            if progress_callback is not None:
                progress_callback("products", product_count, len(products))

        pruned_count = prune_deleted_products(conn, products)

        conn.commit()
        finish_run(
            conn,
            run_id,
            status="success",
            products_synced=product_count,
            variants_synced=variant_count,
            images_synced=image_count,
            metafields_synced=metafield_count,
        )
        return {
            "db_path": str(db_path),
            "products_synced": product_count,
            "variants_synced": variant_count,
            "images_synced": image_count,
            "metafields_synced": metafield_count,
            "products_pruned": pruned_count,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()


def sync_product(db_path: Path, product_id: str) -> dict:
    conn = open_db(db_path)
    run_id = start_run(conn)
    synced_at = now_iso()
    try:
        product = fetch_product_by_id(product_id)
        if not product:
            raise RuntimeError(f"Product not found in Shopify: {product_id}")

        all_metaobject_ids = sorted(
            {
                metaobject_id
                for refs_column in [
                    metafield_reference_json(product, "shopify", "battery-type"),
                    metafield_reference_json(product, "shopify", "coil-connection"),
                    metafield_reference_json(product, "shopify", "color-pattern"),
                    metafield_reference_json(product, "shopify", "e-cigarette-vaporizer-style"),
                    metafield_reference_json(product, "shopify", "e-liquid-flavor"),
                    metafield_reference_json(product, "shopify", "vaping-style"),
                ]
                for metaobject_id in parse_json_list(refs_column)
            }
        )
        for start in range(0, len(all_metaobject_ids), 100):
            batch_ids = all_metaobject_ids[start:start + 100]
            upsert_metaobjects(conn, fetch_metaobjects_by_ids(batch_ids), synced_at)

        variant_count, image_count, metafield_count = upsert_product(conn, product, synced_at)
        resolve_product_metaobject_labels(
            conn,
            product["id"],
            {
                "battery_type_refs_json": metafield_reference_json(product, "shopify", "battery-type"),
                "coil_connection_refs_json": metafield_reference_json(product, "shopify", "coil-connection"),
                "color_pattern_refs_json": metafield_reference_json(product, "shopify", "color-pattern"),
                "vaporizer_style_refs_json": metafield_reference_json(product, "shopify", "e-cigarette-vaporizer-style"),
                "e_liquid_flavor_refs_json": metafield_reference_json(product, "shopify", "e-liquid-flavor"),
                "vaping_style_refs_json": metafield_reference_json(product, "shopify", "vaping-style"),
            },
        )

        conn.commit()
        finish_run(
            conn,
            run_id,
            status="success",
            products_synced=1,
            variants_synced=variant_count,
            images_synced=image_count,
            metafields_synced=metafield_count,
        )
        return {
            "db_path": str(db_path),
            "products_synced": 1,
            "variants_synced": variant_count,
            "images_synced": image_count,
            "metafields_synced": metafield_count,
            "synced_at": synced_at,
            "run_id": run_id,
        }
    except Exception as exc:
        conn.rollback()
        finish_run(conn, run_id, status="failed", error_message=str(exc))
        raise
    finally:
        conn.close()
