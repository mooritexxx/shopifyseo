"""``product_images`` primary key: (product_shopify_id, shopify_id).

Shopify lets one MediaImage be referenced by several products (for example
``fileUpdate(referencesToAdd: ...)``). The catalog sync used to key
``product_images`` on the media id alone, so the second product that carries a
shared image failed with a duplicate-key error and aborted the whole Shopify
stage. The gallery row is per product, so the key is per product + media.

``migrate_product_images_pk`` upgrades an existing table in place. It is
idempotent and a no-op once the composite key is present. SQLite catalogs are
migrated from ``ensure_schema``; production Postgres (whose schema the cutover
owns) is migrated explicitly with ``scripts/migrate_product_images_pk.py``.
"""
from __future__ import annotations

from typing import Any

from shopifyseo.db import (
    Backend,
    backend_for_connection,
    execute,
    primary_key_columns,
    table_columns,
    table_exists,
)

TABLE = "product_images"
TARGET_PK: tuple[str, ...] = ("product_shopify_id", "shopify_id")
PG_PK_NAME = "product_images_pkey"

_SQLITE_CREATE = """
CREATE TABLE product_images (
  shopify_id TEXT NOT NULL,
  product_shopify_id TEXT NOT NULL,
  position INTEGER,
  alt_text TEXT,
  url TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  raw_json TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  PRIMARY KEY (product_shopify_id, shopify_id),
  FOREIGN KEY(product_shopify_id) REFERENCES products(shopify_id) ON DELETE CASCADE
)
"""
_SQLITE_COLUMNS = (
    "shopify_id",
    "product_shopify_id",
    "position",
    "alt_text",
    "url",
    "width",
    "height",
    "raw_json",
    "synced_at",
)


def _is_postgres(conn: Any) -> bool:
    try:
        return backend_for_connection(conn) == Backend.POSTGRES
    except Exception:
        return False


def product_images_pk(conn: Any) -> tuple[tuple[str, ...], str | None]:
    """Return (primary key columns in order, constraint name or None)."""
    return primary_key_columns(conn, TABLE)


def needs_migration(conn: Any) -> bool:
    if not table_exists(conn, TABLE):
        return False
    cols, _ = product_images_pk(conn)
    return tuple(cols) != TARGET_PK


def migrate_product_images_pk(conn: Any) -> bool:
    """Move ``product_images`` onto the composite key. Returns True if it changed anything."""
    if not needs_migration(conn):
        return False
    if _is_postgres(conn):
        _, name = product_images_pk(conn)
        null_rows = execute(
            conn,
            f"SELECT COUNT(*) FROM {TABLE} WHERE product_shopify_id IS NULL"
        ).fetchone()[0]
        if null_rows:
            raise RuntimeError(
                f"{TABLE} has {null_rows} rows without product_shopify_id; "
                "resolve them before switching the primary key"
            )
        execute(conn, f"ALTER TABLE {TABLE} ALTER COLUMN product_shopify_id SET NOT NULL")
        if name:
            execute(conn, f'ALTER TABLE {TABLE} DROP CONSTRAINT "{name}"')
        execute(
            conn,
            f"ALTER TABLE {TABLE} ADD CONSTRAINT {PG_PK_NAME} "
            f"PRIMARY KEY ({', '.join(TARGET_PK)})"
        )
        conn.commit()
        return True

    existing = table_columns(conn, TABLE)
    copy_cols = [c for c in _SQLITE_COLUMNS if c in existing]
    col_list = ", ".join(copy_cols)
    conn.commit()
    execute(conn, f"ALTER TABLE {TABLE} RENAME TO {TABLE}__pk_old")
    execute(conn, _SQLITE_CREATE)
    execute(
        conn,
        f"INSERT INTO {TABLE} ({col_list}) "
        f"SELECT {col_list} FROM {TABLE}__pk_old WHERE product_shopify_id IS NOT NULL"
    )
    execute(conn, f"DROP TABLE {TABLE}__pk_old")
    execute(
        conn,
        f"CREATE INDEX IF NOT EXISTS idx_images_product ON {TABLE}(product_shopify_id)"
    )
    conn.commit()
    return True
