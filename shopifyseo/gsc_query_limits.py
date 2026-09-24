"""Shared GSC per-URL constants.

Used by: Search Analytics API fetch, `gsc_query_rows` reads for AI context,
`gsc_queries` embedding bundle text. Keeps prompts, DB cache, and vectors aligned.
"""

GSC_PER_URL_QUERY_ROW_LIMIT = 20

# Reporting window for the per-URL Search Console numbers denormalized onto catalog rows.
#
# Month-to-date is a yardstick that changes length every day: on the 1st it covers a
# single day, which Google has not published yet (2-3 day lag), so every catalog row's
# search numbers collapsed at the start of each month and grew back through it. That also
# made pages incomparable depending on which day you looked.
#
# A rolling 30-day window is always the same length, and lines up with the trailing
# 28-day window the GA4 columns beside it already use.
#
# This governs catalog rows only. The Overview screen keeps its own period selector,
# including month-to-date with its month-over-month comparison.
GSC_CATALOG_PERIOD_MODE = "rolling_30d"
