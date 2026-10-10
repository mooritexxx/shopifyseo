import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shopifyseo.dashboard_google._auth import get_search_data_access_token, google_api_get
from shopifyseo.db import get_connection

conn = get_connection(path="/Users/home/Projects/shopifyseo/shopify_catalog.sqlite3")
try:
    token = get_search_data_access_token(conn)
    url_v = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?url=https%3A%2F%2Fvapely.ca%2F&strategy=mobile"
    url_fb = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?url=https%3A%2F%2Fexample.com%2F&strategy=mobile"
    print("Vapely:")
    try:
        google_api_get(url_v, token)
        print("Vapely Success")
    except Exception as e:
        print("Vapely Error:", type(e), getattr(e, "status", None), getattr(e, "body", None))
except Exception as e:
    print("Token Error:", e)
