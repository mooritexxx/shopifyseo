"""Read-only, conservative before/after reporting for confirmed opportunity saves."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote


def change_results(conn, today=None):
    today = today or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    items = []
    rows = conn.execute('''SELECT t.id,t.object_type,t.object_handle,t.status,e.applied_at
        FROM seo_opportunity_tasks t LEFT JOIN seo_change_events e ON e.task_id=t.id
        WHERE t.status IN ('applied','monitoring') ORDER BY e.applied_at DESC,t.id DESC LIMIT 10''')
    for row in rows:
        item = dict(row)
        prefix = {'product':'products','collection':'collections','page':'pages','blog_article':'articles'}[row['object_type']]
        item['detail_url'] = '/' + prefix + '/' + quote(row['object_handle'], safe='/' if row['object_type']=='blog_article' else '')
        item.update(state='unknown_date', applied_date=None, before=None, after=None)
        if row['applied_at']:
            stamp = datetime.fromisoformat(row['applied_at']).replace(tzinfo=timezone.utc)
            applied = stamp.astimezone(ZoneInfo('America/Los_Angeles')).date()
            item['applied_date'] = applied.isoformat()
            before_start, before_end = applied-timedelta(days=14), applied-timedelta(days=1)
            after_start, after_end = applied+timedelta(days=1), applied+timedelta(days=14)
            def window(start, end):
                stats = conn.execute('''SELECT COUNT(DISTINCT date) AS days, COALESCE(SUM(clicks),0) AS clicks,
                    COALESCE(SUM(impressions),0) AS impressions FROM gsc_page_daily
                    WHERE object_type=? AND object_handle=? AND date BETWEEN ? AND ?''',
                    (row['object_type'],row['object_handle'],start.isoformat(),end.isoformat())).fetchone()
                return dict(start=start.isoformat(), end=end.isoformat(), days=stats['days'], clicks=stats['clicks'],
                            impressions=stats['impressions'], ctr=stats['clicks']/stats['impressions'] if stats['impressions'] else None)
            item['before'], item['after'] = window(before_start,before_end), window(after_start,after_end)
            # Missing dates may be zero-traffic days or gaps in collection; do not fabricate a baseline.
            item['state'] = ('waiting' if today < after_end+timedelta(days=3) else
                             'ready' if all(w['days']==14 and w['impressions']>0 for w in (item['before'],item['after'])) else 'insufficient')
        items.append(item)
    return {'items':items, 'window_days':14, 'reporting_lag_days':3}
