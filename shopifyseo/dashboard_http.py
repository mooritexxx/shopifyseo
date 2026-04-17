import json
import urllib.parse

import requests


SESSION = requests.Session()
HTTP_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
SESSION.mount("http://", HTTP_ADAPTER)
SESSION.mount("https://", HTTP_ADAPTER)


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
) -> str:
    try:
        response = SESSION.request(method=method, url=url, headers=headers or {}, data=data, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.HTTPError as exc:
        response = exc.response
        body = response.text if response is not None else ""
        raise HttpRequestError(
            f"HTTP {response.status_code if response is not None else 'unknown'} for {url}",
            status=response.status_code if response is not None else None,
            body=body,
            reason=str(exc),
            headers=dict(response.headers) if response is not None else {},
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
) -> dict:
    data = None
    merged_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    text = request_text(url, method=method, headers=merged_headers, data=data, timeout=timeout)
    return json.loads(text)
