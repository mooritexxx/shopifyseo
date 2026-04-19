import sqlite3
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
sys.path.insert(0, '/Users/home/Projects/shopifyseo')
from shopifyseo.dashboard_google._auth import get_google_access_token, google_api_get, HttpRequestError

conn = sqlite3.connect('/Users/home/Projects/shopifyseo/shopify_catalog.sqlite3')
conn.row_factory = sqlite3.Row
token = get_google_access_token(conn)

cursor = conn.cursor()
cursor.execute("SELECT handle FROM shopify_products LIMIT 20")
handles = [r[0] for r in cursor.fetchall()]

urls = [f"https://vapely.ca/products/{h}" for h in handles]

print(f"Blasting Google PageSpeed with {len(urls)} concurrent requests explicitly to capture RAW errors...")

def fetch(u):
    enc = urllib.parse.quote_plus(u)
    api_url = f"https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?url={enc}&strategy=mobile"
    try:
        google_api_get(api_url, token, timeout=120)
        return "200 OK"
    except HttpRequestError as e:
        return f"HTTP {e.status}: {e.body[:200] if e.body else 'No body'}"
    except Exception as e:
        return f"Exception: {e}"

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
    print(f"Captured {len(issues)} absolute RAW Google API error responses.")
