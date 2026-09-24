import json
import urllib.parse

import requests
from urllib3.util.retry import Retry


# Transient 429/5xx responses previously dropped that target's data point for the whole
# run. Retry with exponential backoff, honouring Retry-After when the server sends it.
# PageSpeed keeps its own bespoke 429 handling on top of this.
_RETRY_KWARGS = dict(
    total=3,
    connect=3,
    read=3,
    status=3,
    backoff_factor=0.5,
    status_forcelist=(429, 500, 502, 503, 504),
    respect_retry_after_header=True,
    raise_on_status=False,
)

# Default session: never auto-retries POST. Shopify mutations (article creation, SEO
# updates) share this session, and replaying one after a 5xx could double-apply it.
HTTP_RETRY = Retry(allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}), **_RETRY_KWARGS)

# Opt-in session for POST endpoints that are semantically reads and therefore safe to
# replay — the Google Search Console / GA4 / PageSpeed query APIs.
HTTP_RETRY_IDEMPOTENT_POST = Retry(
    allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "POST"}), **_RETRY_KWARGS
)


def _build_session(retry: Retry) -> requests.Session:
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=100, pool_maxsize=100, max_retries=retry
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


SESSION = _build_session(HTTP_RETRY)
IDEMPOTENT_POST_SESSION = _build_session(HTTP_RETRY_IDEMPOTENT_POST)
HTTP_ADAPTER = SESSION.get_adapter("https://")


class HttpRequestError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str = "", reason: str = "", headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.reason = reason
        self.headers = headers or {}


def request_text(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    data: bytes | None = None,
    timeout: int = 30,
    idempotent: bool = False,
) -> str:
    """Perform an HTTP request.

    Set ``idempotent=True`` only for endpoints that are safe to replay (read-only POST
    query APIs); it enables automatic retry of POST on 429/5xx.
    """
    session = IDEMPOTENT_POST_SESSION if idempotent else SESSION
    try:
        response = session.request(method=method, url=url, headers=headers or {}, data=data, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.HTTPError as exc:
        response = exc.response
        if response is None:
            raise HttpRequestError(f"HTTP unknown for {url}", reason=str(exc)) from exc
        raise HttpRequestError(
            f"HTTP {response.status_code} for {url}",
            status=response.status_code,
            body=response.text,
            reason=str(exc),
            headers=dict(response.headers),
        ) from exc
    except requests.RequestException as exc:
        raise HttpRequestError(f"Connection error for {url}: {exc}", reason=str(exc)) from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    payload: dict | None = None,
    form: dict | None = None,
    timeout: int = 30,
    idempotent: bool = False,
) -> dict:
    data = None
    merged_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    text = request_text(
        url, method=method, headers=merged_headers, data=data, timeout=timeout, idempotent=idempotent
    )
    return json.loads(text)
