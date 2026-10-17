#!/usr/bin/env python3
"""Switch ``product_images`` to PRIMARY KEY (product_shopify_id, shopify_id).

Needed once on production Postgres (the cutover owns that schema, so the app
does not run this DDL on its own). Without it, the Shopify catalog sync fails
with a duplicate key on ``product_images`` whenever one MediaImage is attached
to more than one product.

Dry run by default; pass ``--apply`` to change the schema. Idempotent.

    DATABASE_URL=postgresql://... python scripts/migrate_product_images_pk.py
    DATABASE_URL=postgresql://... python scripts/migrate_product_images_pk.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shopifyseo.dashboard_store import DB_PATH  # noqa: E402
from shopifyseo.db import get_connection  # noqa: E402
from shopifyseo.shopify_catalog_sync.image_pk import (  # noqa: E402
    TARGET_PK,
    migrate_product_images_pk,
    needs_migration,
    product_images_pk,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="change the schema (default: dry run)")
    args = parser.parse_args()

    conn = get_connection(path=DB_PATH)
    try:
        cols, name = product_images_pk(conn)
        print(f"product_images primary key now: {cols or '(none)'} constraint={name}")
        if not needs_migration(conn):
            print(f"already {TARGET_PK}; nothing to do")
            return 0
        if not args.apply:
            print(f"dry run: would switch to {TARGET_PK}; rerun with --apply")
            return 0
        migrate_product_images_pk(conn)
        cols, name = product_images_pk(conn)
        print(f"migrated: product_images primary key is {cols} constraint={name}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
