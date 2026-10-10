import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shopifyseo.dashboard_google._auth import get_google_access_token, google_token_has_scope
from shopifyseo.dashboard_http import HttpRequestError, request_json
from shopifyseo.db import get_connection


def _pagespeed_url(page_url: str) -> str:
    params = {"url": page_url, "strategy": "mobile"}
    api_key = (os.getenv("PAGESPEED_API_KEY") or "").strip()
    if api_key:
        params["key"] = api_key
    return "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?" + urlencode(params)


def _pagespeed_get(api_url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return request_json(api_url, headers=headers, method="GET", timeout=120)


conn = get_connection(path="/Users/home/Projects/shopifyseo/shopify_catalog.sqlite3")
token = ""
if google_token_has_scope(conn, "openid"):
    try:
        token = get_google_access_token(conn)
    except Exception as e:
        print("OAuth token unavailable, using public quota:", type(e).__name__)

cursor = conn.cursor()
cursor.execute("SELECT handle FROM shopify_products LIMIT 20")
handles = [r[0] for r in cursor.fetchall()]

urls = [f"https://vapely.ca/products/{h}" for h in handles]

print(f"Blasting Google PageSpeed with {len(urls)} concurrent requests to capture error statuses...")


def fetch(u):
    api_url = _pagespeed_url(u)
    try:
        _pagespeed_get(api_url, token)
        return "200 OK"
    except HttpRequestError as e:
        return f"HTTP {e.status}: {type(e).__name__}"
    except Exception as e:
        return f"Exception: {type(e).__name__}"


issues = []
with ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(fetch, u): u for u in urls}
    for future in as_completed(futures):
        res = future.result()
        if "200" not in res:
            issues.append(res)
            print(res)

if not issues:
    print("All 20 requests succeeded successfully. Google API returned no errors.")
else:
    print(f"Captured {len(issues)} PageSpeed error statuses.")
