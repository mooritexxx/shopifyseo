"""Read-only technical recrawl worklist over denormalized inspection evidence."""
import json

from backend.app.db import open_db_connection
from shopifyseo.dashboard_queries._urls import object_url, blog_article_composite_handle
from shopifyseo.index_evidence import TABLES, timestamp


def recrawl_candidates(flag='stale_robots_block'):
    conn = open_db_connection()
    try:
        links = {}
        for row in conn.execute("SELECT url,payload_json FROM google_api_cache WHERE cache_type='url_inspection' ORDER BY fetched_at"):
            try:
                links[row['url']] = (json.loads(row['payload_json']).get('inspectionResult') or {}).get('inspectionResultLink')
            except (ValueError, TypeError):
                continue
        ranked = []
        for kind, table in TABLES.items():
            specific = 'vendor, total_inventory' if kind == 'product' else "'' AS vendor, 0 AS total_inventory"
            blog = ', blog_handle' if kind == 'blog_article' else ''
            rows = conn.execute(f'''SELECT handle,index_coverage,index_last_crawl_at,index_last_fetched_at,
                index_flag_reason,gsc_impressions,{specific}{blog} FROM {table} WHERE index_flag=?''', (flag,))
            for row in rows:
                handle = blog_article_composite_handle(row['blog_handle'], row['handle']) if kind == 'blog_article' else row['handle']
                url = object_url(kind, handle)
                in_stock = (row['total_inventory'] or 0) > 0
                item = dict(url=url, handle=handle, vendor=row['vendor'] or '', in_stock=in_stock,
                            coverage=row['index_coverage'] or '', crawled_at=row['index_last_crawl_at'],
                            inspected_at=row['index_last_fetched_at'], inspect_href=links.get(url),
                            flag_reason=row['index_flag_reason'] or '')
                ranked.append(((not in_stock, -(row['gsc_impressions'] or 0), timestamp(row['index_last_crawl_at']) or 0, url), item))
        return [item for _, item in sorted(ranked, key=lambda x: x[0])]
    finally:
        conn.close()
