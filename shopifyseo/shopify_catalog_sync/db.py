import json
import os
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..shopify_admin import graphql_post, graphql_request
from ..sqlite_utf8 import configure_sqlite_text_decode
from .queries import (
    PRODUCTS_QUERY,
    PRODUCT_QUERY,
    COLLECTIONS_QUERY,
    COLLECTION_QUERY,
    PAGES_QUERY,
    PAGE_QUERY,
    ARTICLE_QUERY,
    BLOGS_QUERY,
    BLOG_QUERY,
    BLOG_ARTICLES_CONNECTION_QUERY,
    ARTICLES_BY_BLOG_QUERY,
    COLLECTION_PRODUCTS_QUERY,
    METAOBJECTS_BY_IDS_QUERY,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def metafield_value(product: dict, namespace: str, key: str) -> str:
    metafields = [edge["node"] for edge in (product.get("metafields") or {}).get("edges", [])]
    for metafield in metafields:
        if metafield.get("namespace") == namespace and metafield.get("key") == key:
            return metafield.get("value") or ""
    return ""


def metafield_reference_json(product: dict, namespace: str, key: str) -> str:
    value = metafield_value(product, namespace, key).strip()
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json_dumps(parsed)


def parse_json_list(value: str) -> list[str]:
    if not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def resolve_metaobject_label(metaobject: dict) -> str:
    display_name = str(metaobject.get("displayName") or "").strip()
    if display_name:
        return display_name
    handle = str(metaobject.get("handle") or "").strip()
    if handle:
        return handle
    for field in metaobject.get("fields") or []:
        key = str(field.get("key") or "").strip().lower()
        value = str(field.get("value") or "").strip()
        if key in {"label", "name", "title", "value"} and value:
            return value
    return ""


def fetch_metaobjects_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    try:
        data = graphql_request(METAOBJECTS_BY_IDS_QUERY, {"ids": ids})
    except SystemExit as exc:
        if "read_metaobjects" in str(exc):
            return []
        raise
    nodes = data["data"]["nodes"] or []
    return [node for node in nodes if node]


def upsert_metaobjects(conn: sqlite3.Connection, metaobjects: list[dict], synced_at: str) -> None:
    if not metaobjects:
        return
    conn.executemany(
        """
        INSERT INTO shopify_metaobjects (
          shopify_id,
          type,
          handle,
          display_name,
          fields_json,
          raw_json,
          updated_at,
          synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(shopify_id) DO UPDATE SET
          type = excluded.type,
          handle = excluded.handle,
          display_name = excluded.display_name,
          fields_json = excluded.fields_json,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at,
          synced_at = excluded.synced_at
        """,
        [
            (
                metaobject["id"],
                metaobject.get("type") or "",
                metaobject.get("handle") or "",
                resolve_metaobject_label(metaobject),
                json_dumps(metaobject.get("fields") or []),
                json_dumps(metaobject),
                metaobject.get("updatedAt") or "",
                synced_at,
            )
            for metaobject in metaobjects
        ],
    )


def resolve_product_metaobject_labels(conn: sqlite3.Connection, product_id: str, refs_by_field: dict[str, str]) -> None:
    label_columns = {
        "battery_type_refs_json": "battery_type_labels_json",
        "coil_connection_refs_json": "coil_connection_labels_json",
        "color_pattern_refs_json": "color_pattern_labels_json",
        "vaporizer_style_refs_json": "vaporizer_style_labels_json",
        "e_liquid_flavor_refs_json": "e_liquid_flavor_labels_json",
        "vaping_style_refs_json": "vaping_style_labels_json",
    }
    resolved_payload: dict[str, str] = {}
    for refs_column, labels_column in label_columns.items():
        ids = parse_json_list(refs_by_field.get(refs_column, ""))
        if not ids:
            resolved_payload[labels_column] = ""
            continue
        rows = conn.execute(
            f"SELECT shopify_id, display_name FROM shopify_metaobjects WHERE shopify_id IN ({','.join('?' for _ in ids)})",
            ids,
        ).fetchall()
        label_map = {row["shopify_id"]: row["display_name"] or "" for row in rows}
        labels = [label_map.get(metaobject_id, "") for metaobject_id in ids]
        labels = [label for label in labels if label]
        resolved_payload[labels_column] = json_dumps(labels) if labels else ""
    conn.execute(
        """
        UPDATE products
        SET battery_type_labels_json = ?,
            coil_connection_labels_json = ?,
            color_pattern_labels_json = ?,
            vaporizer_style_labels_json = ?,
            e_liquid_flavor_labels_json = ?,
            vaping_style_labels_json = ?
        WHERE shopify_id = ?
        """,
        (
            resolved_payload["battery_type_labels_json"],
            resolved_payload["coil_connection_labels_json"],
            resolved_payload["color_pattern_labels_json"],
            resolved_payload["vaporizer_style_labels_json"],
            resolved_payload["e_liquid_flavor_labels_json"],
            resolved_payload["vaping_style_labels_json"],
            product_id,
        ),
    )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS sync_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT NOT NULL,
          products_synced INTEGER NOT NULL DEFAULT 0,
          variants_synced INTEGER NOT NULL DEFAULT 0,
          images_synced INTEGER NOT NULL DEFAULT 0,
          metafields_synced INTEGER NOT NULL DEFAULT 0,
          error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS products (
          shopify_id TEXT PRIMARY KEY,
          legacy_resource_id TEXT,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          vendor TEXT,
          product_type TEXT,
          status TEXT,
          created_at TEXT,
          updated_at TEXT,
          published_at TEXT,
          description_html TEXT,
          tags_json TEXT NOT NULL,
          seo_title TEXT,
          seo_description TEXT,
          total_inventory INTEGER,
          tracks_inventory INTEGER,
          category_full_name TEXT,
          battery_size TEXT,
          charging_port TEXT,
          coil TEXT,
          custom_collection TEXT,
          device_type TEXT,
          nicotine_strength TEXT,
          puff_count TEXT,
          size TEXT,
          battery_type_refs_json TEXT,
          coil_connection_refs_json TEXT,
          color_pattern_refs_json TEXT,
          vaporizer_style_refs_json TEXT,
          e_liquid_flavor_refs_json TEXT,
          vaping_style_refs_json TEXT,
          battery_type_labels_json TEXT,
          coil_connection_labels_json TEXT,
          color_pattern_labels_json TEXT,
          vaporizer_style_labels_json TEXT,
          e_liquid_flavor_labels_json TEXT,
          vaping_style_labels_json TEXT,
          online_store_url TEXT,
          options_json TEXT NOT NULL,
          featured_image_json TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_variants (
          shopify_id TEXT PRIMARY KEY,
          product_shopify_id TEXT NOT NULL,
          legacy_resource_id TEXT,
          title TEXT NOT NULL,
          sku TEXT,
          barcode TEXT,
          price TEXT,
          compare_at_price TEXT,
          position INTEGER,
          inventory_policy TEXT,
          inventory_quantity INTEGER,
          taxable INTEGER,
          selected_options_json TEXT NOT NULL,
          image_json TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL,
          FOREIGN KEY(product_shopify_id) REFERENCES products(shopify_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_images (
          shopify_id TEXT PRIMARY KEY,
          product_shopify_id TEXT NOT NULL,
          position INTEGER,
          alt_text TEXT,
          url TEXT NOT NULL,
          width INTEGER,
          height INTEGER,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL,
          FOREIGN KEY(product_shopify_id) REFERENCES products(shopify_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_metafields (
          shopify_id TEXT PRIMARY KEY,
          product_shopify_id TEXT NOT NULL,
          namespace TEXT NOT NULL,
          key TEXT NOT NULL,
          type TEXT,
          value TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL,
          FOREIGN KEY(product_shopify_id) REFERENCES products(shopify_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_products_vendor ON products(vendor);
        CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
        CREATE INDEX IF NOT EXISTS idx_variants_product ON product_variants(product_shopify_id);
        CREATE INDEX IF NOT EXISTS idx_images_product ON product_images(product_shopify_id);
        CREATE INDEX IF NOT EXISTS idx_metafields_product ON product_metafields(product_shopify_id);

        CREATE TABLE IF NOT EXISTS product_image_file_cache (
          image_shopify_id TEXT PRIMARY KEY,
          normalized_url TEXT NOT NULL,
          local_relpath TEXT NOT NULL,
          etag TEXT,
          last_modified TEXT,
          content_length INTEGER,
          sha256_hex TEXT NOT NULL,
          mime TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_metafields_ns_key ON product_metafields(namespace, key);

        CREATE TABLE IF NOT EXISTS collections (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          updated_at TEXT,
          description_html TEXT,
          seo_title TEXT,
          seo_description TEXT,
          rule_set_json TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS collection_metafields (
          shopify_id TEXT PRIMARY KEY,
          collection_shopify_id TEXT NOT NULL,
          namespace TEXT NOT NULL,
          key TEXT NOT NULL,
          type TEXT,
          value TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL,
          FOREIGN KEY(collection_shopify_id) REFERENCES collections(shopify_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS pages (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          updated_at TEXT,
          body TEXT,
          seo_title TEXT,
          seo_description TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blogs (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          created_at TEXT,
          updated_at TEXT,
          comment_policy TEXT,
          tags_json TEXT NOT NULL,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blog_articles (
          shopify_id TEXT PRIMARY KEY,
          blog_shopify_id TEXT NOT NULL,
          blog_handle TEXT NOT NULL,
          title TEXT NOT NULL,
          handle TEXT NOT NULL,
          published_at TEXT,
          updated_at TEXT,
          is_published INTEGER NOT NULL DEFAULT 0,
          body TEXT,
          summary TEXT,
          tags_json TEXT NOT NULL,
          author_name TEXT,
          seo_title TEXT,
          seo_description TEXT,
          image_json TEXT,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL,
          FOREIGN KEY(blog_shopify_id) REFERENCES blogs(shopify_id) ON DELETE CASCADE,
          UNIQUE(blog_shopify_id, handle)
        );

        CREATE TABLE IF NOT EXISTS collection_products (
          collection_shopify_id TEXT NOT NULL,
          product_shopify_id TEXT NOT NULL,
          product_handle TEXT,
          product_title TEXT,
          synced_at TEXT NOT NULL,
          PRIMARY KEY(collection_shopify_id, product_shopify_id),
          FOREIGN KEY(collection_shopify_id) REFERENCES collections(shopify_id) ON DELETE CASCADE,
          FOREIGN KEY(product_shopify_id) REFERENCES products(shopify_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_collections_handle ON collections(handle);
        CREATE INDEX IF NOT EXISTS idx_collection_metafields_collection ON collection_metafields(collection_shopify_id);
        CREATE INDEX IF NOT EXISTS idx_collection_metafields_ns_key ON collection_metafields(namespace, key);
        CREATE INDEX IF NOT EXISTS idx_collection_products_collection ON collection_products(collection_shopify_id);
        CREATE INDEX IF NOT EXISTS idx_collection_products_product ON collection_products(product_shopify_id);
        CREATE INDEX IF NOT EXISTS idx_pages_handle ON pages(handle);
        CREATE INDEX IF NOT EXISTS idx_blogs_handle ON blogs(handle);
        CREATE INDEX IF NOT EXISTS idx_blog_articles_blog ON blog_articles(blog_shopify_id);
        CREATE INDEX IF NOT EXISTS idx_blog_articles_blog_handle ON blog_articles(blog_handle, handle);

        CREATE TABLE IF NOT EXISTS shopify_metaobjects (
          shopify_id TEXT PRIMARY KEY,
          type TEXT,
          handle TEXT,
          display_name TEXT,
          fields_json TEXT NOT NULL,
          raw_json TEXT NOT NULL,
          updated_at TEXT,
          synced_at TEXT NOT NULL
        );
        """
    )
    ensure_column(conn, "sync_runs", "collections_synced", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sync_runs", "collection_metafields_synced", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sync_runs", "pages_synced", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sync_runs", "blogs_synced", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sync_runs", "blog_articles_synced", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "sync_runs", "collection_products_synced", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "products", "battery_size", "TEXT")
    ensure_column(conn, "products", "charging_port", "TEXT")
    ensure_column(conn, "products", "coil", "TEXT")
    ensure_column(conn, "products", "custom_collection", "TEXT")
    ensure_column(conn, "products", "device_type", "TEXT")
    ensure_column(conn, "products", "nicotine_strength", "TEXT")
    ensure_column(conn, "products", "puff_count", "TEXT")
    ensure_column(conn, "products", "size", "TEXT")
    ensure_column(conn, "products", "battery_type_refs_json", "TEXT")
    ensure_column(conn, "products", "coil_connection_refs_json", "TEXT")
    ensure_column(conn, "products", "color_pattern_refs_json", "TEXT")
    ensure_column(conn, "products", "vaporizer_style_refs_json", "TEXT")
    ensure_column(conn, "products", "e_liquid_flavor_refs_json", "TEXT")
    ensure_column(conn, "products", "vaping_style_refs_json", "TEXT")
    ensure_column(conn, "products", "battery_type_labels_json", "TEXT")
    ensure_column(conn, "products", "coil_connection_labels_json", "TEXT")
    ensure_column(conn, "products", "color_pattern_labels_json", "TEXT")
    ensure_column(conn, "products", "vaporizer_style_labels_json", "TEXT")
    ensure_column(conn, "products", "e_liquid_flavor_labels_json", "TEXT")
    ensure_column(conn, "products", "vaping_style_labels_json", "TEXT")
    ensure_column(conn, "product_images", "position", "INTEGER")
    ensure_column(conn, "collections", "image_json", "TEXT")
    ensure_column(conn, "pages", "template_suffix", "TEXT")
    ensure_column(conn, "pages", "template_images_json", "TEXT")
    conn.commit()


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    configure_sqlite_text_decode(conn)
    ensure_schema(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def start_run(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        UPDATE sync_runs
        SET finished_at = COALESCE(finished_at, ?),
            status = CASE WHEN status = 'running' THEN 'abandoned' ELSE status END,
            error_message = CASE
              WHEN status = 'running' AND COALESCE(error_message, '') = '' THEN 'Superseded by a newer sync run'
              ELSE error_message
            END
        WHERE status = 'running'
        """,
        (now_iso(),),
    )
    cur = conn.execute(
        """
        INSERT INTO sync_runs(started_at, status)
        VALUES(?, 'running')
        """,
        (now_iso(),),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    products_synced: int = 0,
    variants_synced: int = 0,
    images_synced: int = 0,
    metafields_synced: int = 0,
    collections_synced: int = 0,
    collection_metafields_synced: int = 0,
    pages_synced: int = 0,
    blogs_synced: int = 0,
    blog_articles_synced: int = 0,
    collection_products_synced: int = 0,
    error_message: str = "",
) -> None:
    conn.execute(
        """
        UPDATE sync_runs
        SET finished_at = ?,
            status = ?,
            products_synced = ?,
            variants_synced = ?,
            images_synced = ?,
            metafields_synced = ?,
            collections_synced = ?,
            collection_metafields_synced = ?,
            pages_synced = ?,
            blogs_synced = ?,
            blog_articles_synced = ?,
            collection_products_synced = ?,
            error_message = ?
        WHERE id = ?
        """,
        (
            now_iso(),
            status,
            products_synced,
            variants_synced,
            images_synced,
            metafields_synced,
            collections_synced,
            collection_metafields_synced,
            pages_synced,
            blogs_synced,
            blog_articles_synced,
            collection_products_synced,
            error_message,
            run_id,
        ),
    )
    conn.commit()


def fetch_all_products(
    page_size: int,
    *,
    after_page: Callable[[int], None] | None = None,
) -> list[dict]:
    products: list[dict] = []
    cursor = None
    while True:
        data = graphql_request(PRODUCTS_QUERY, {"first": page_size, "after": cursor})
        connection = data["data"]["products"]
        products.extend(edge["node"] for edge in connection["edges"])
        if after_page is not None:
            after_page(len(products))
        if not connection["pageInfo"]["hasNextPage"]:
            return products
        cursor = connection["pageInfo"]["endCursor"]


def fetch_all_collections(page_size: int) -> list[dict]:
    collections: list[dict] = []
    cursor = None
    while True:
        data = graphql_request(COLLECTIONS_QUERY, {"first": page_size, "after": cursor})
        connection = data["data"]["collections"]
        collections.extend(edge["node"] for edge in connection["edges"])
        if not connection["pageInfo"]["hasNextPage"]:
            return collections
        cursor = connection["pageInfo"]["endCursor"]


def fetch_all_pages(page_size: int) -> list[dict]:
    pages: list[dict] = []
    cursor = None
    while True:
        data = graphql_request(PAGES_QUERY, {"first": page_size, "after": cursor})
        connection = data["data"]["pages"]
        pages.extend(edge["node"] for edge in connection["edges"])
        if not connection["pageInfo"]["hasNextPage"]:
            return pages
        cursor = connection["pageInfo"]["endCursor"]


def fetch_all_blogs(page_size: int) -> list[dict]:
    blogs: list[dict] = []
    cursor = None
    while True:
        data = graphql_request(BLOGS_QUERY, {"first": page_size, "after": cursor})
        connection = (data.get("data") or {}).get("blogs") or {}
        edges = connection.get("edges") or []
        blogs.extend(edge["node"] for edge in edges if edge.get("node"))
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return blogs
        cursor = page_info.get("endCursor")


def fetch_blog_by_id(blog_id: str) -> dict | None:
    data = graphql_request(BLOG_QUERY, {"id": blog_id})
    return data["data"]["blog"]


def _fetch_articles_via_blog_connection(blog_id: str, page_size: int) -> list[dict]:
    """Paginate Blog.articles — reliable; avoids root search + nested blog filter issues."""
    articles: list[dict] = []
    cursor = None
    while True:
        data = graphql_request(
            BLOG_ARTICLES_CONNECTION_QUERY,
            {"id": blog_id, "first": page_size, "after": cursor},
        )
        blog = (data.get("data") or {}).get("blog")
        if not blog:
            return articles
        connection = blog.get("articles") or {}
        for edge in connection.get("edges", []) or []:
            node = edge.get("node")
            if node:
                articles.append(node)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return articles
        cursor = page_info.get("endCursor")


def _shopify_gid_numeric_id(gid: str) -> str:
    """Extract numeric ID from gid://shopify/Blog/123456789."""
    if not gid:
        return ""
    return gid.rsplit("/", 1)[-1]


def _fetch_article_pages_for_blog(
    search_query: str, blog_shopify_id: str, page_size: int
) -> list[dict] | None:
    """Collect articles for one blog. None if Shopify returned GraphQL errors (retry other query)."""
    articles: list[dict] = []
    cursor = None
    while True:
        payload = graphql_post(
            ARTICLES_BY_BLOG_QUERY,
            {"first": page_size, "after": cursor, "query": search_query},
        )
        if payload.get("errors"):
            return None
        connection = (payload.get("data") or {}).get("articles") or {}
        for edge in connection.get("edges", []) or []:
            node = edge.get("node")
            if not node:
                continue
            blog = node.get("blog") or {}
            node_blog_id = blog.get("id")
            # If Shopify omits nested blog { id }, do not drop the row — search was scoped by blog_id.
            if node_blog_id is not None and node_blog_id != blog_shopify_id:
                continue
            articles.append(node)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return articles
        cursor = page_info.get("endCursor")


def fetch_all_articles_for_blog(blog_id: str, page_size: int) -> list[dict]:
    primary = _fetch_articles_via_blog_connection(blog_id, page_size)
    if primary:
        return primary
    numeric = _shopify_gid_numeric_id(blog_id)
    if not numeric:
        return []
    search_query = f"blog_id:{numeric} published_status:any"
    fallback_query = f"blog_id:{numeric}"
    result = _fetch_article_pages_for_blog(search_query, blog_id, page_size)
    if result is None:
        result = _fetch_article_pages_for_blog(fallback_query, blog_id, page_size)
    return result if result is not None else []


def fetch_collection_products(collection_id: str, page_size: int) -> list[dict]:
    products: list[dict] = []
    cursor = None
    while True:
        data = graphql_request(
            COLLECTION_PRODUCTS_QUERY,
            {"id": collection_id, "first": page_size, "after": cursor},
        )
        collection = data["data"]["collection"]
        if not collection:
            return products
        connection = collection["products"]
        products.extend(edge["node"] for edge in connection["edges"])
        if not connection["pageInfo"]["hasNextPage"]:
            return products
        cursor = connection["pageInfo"]["endCursor"]


def fetch_collection_by_id(collection_id: str) -> dict | None:
    data = graphql_request(COLLECTION_QUERY, {"id": collection_id})
    return data["data"]["collection"]


def fetch_page_by_id(page_id: str) -> dict | None:
    data = graphql_request(PAGE_QUERY, {"id": page_id})
    return data["data"]["page"]


def fetch_article_by_id(article_id: str) -> dict | None:
    data = graphql_request(ARTICLE_QUERY, {"id": article_id})
    return data["data"]["article"]


def fetch_product_by_id(product_id: str) -> dict | None:
    data = graphql_request(PRODUCT_QUERY, {"id": product_id})
    return data["data"]["product"]


def tags_list_as_json(tags: object | None) -> str:
    if tags is None:
        return "[]"
    if isinstance(tags, list):
        return json_dumps(tags)
    if isinstance(tags, str):
        parts = [part.strip() for part in tags.split(",") if part.strip()]
        return json_dumps(parts)
    return "[]"
