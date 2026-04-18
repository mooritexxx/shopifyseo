"""Shared row cap for GSC per-URL query lists.

Used by: Search Analytics API fetch, `gsc_query_rows` reads for AI context,
`gsc_queries` embedding bundle text. Keeps prompts, DB cache, and vectors aligned.
"""

GSC_PER_URL_QUERY_ROW_LIMIT = 20
