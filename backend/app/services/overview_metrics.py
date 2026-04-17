def summarize_gsc(facts: list[dict]) -> dict:
    pages = [fact for fact in facts if fact["gsc_impressions"] or fact["gsc_clicks"]]
    return {
        "gsc_pages": len(pages),
        "gsc_clicks": int(sum(fact["gsc_clicks"] for fact in pages)),
        "gsc_impressions": int(sum(fact["gsc_impressions"] for fact in pages)),
    }


def summarize_ga4(facts: list[dict]) -> dict:
    rows = [fact for fact in facts if fact["ga4_sessions"] or fact["ga4_views"]]
    sessions = sum(fact["ga4_sessions"] for fact in rows)
    views = sum(fact["ga4_views"] for fact in rows)
    return {
        "ga4_pages": len(rows),
        "ga4_sessions": sessions,
        "ga4_views": views,
    }
