from __future__ import annotations

from backend.app.db import open_db_connection
from shopifyseo.dashboard_queries import _base_store_url
import shopifyseo.dashboard_google as dg


def get_store_info() -> dict[str, str]:
    conn = open_db_connection()
    try:
        store_url = _base_store_url(conn)
        store_name = (dg.get_service_setting(conn, "store_name") or "").strip()
        if not store_name:
            shop = (dg.get_service_setting(conn, "shopify_shop") or "").strip()
            if shop:
                store_name = shop.removesuffix(".myshopify.com")
        store_description = (dg.get_service_setting(conn, "store_description") or "").strip()
        primary_market_country = (dg.get_service_setting(conn, "primary_market_country") or "").strip()
        dashboard_timezone = (dg.get_service_setting(conn, "dashboard_timezone") or "").strip()
        return {
            "store_url": store_url,
            "store_name": store_name,
            "store_description": store_description,
            "primary_market_country": primary_market_country,
            "dashboard_timezone": dashboard_timezone,
        }
    finally:
        conn.close()
