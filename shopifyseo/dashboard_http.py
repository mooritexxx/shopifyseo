import json
import re
import urllib.parse
from dataclasses import dataclass

import requests
from urllib3.util.retry import Retry

_GOOGLE_ERROR_MESSAGE_CAP = 200
_SECRET_IN_TEXT_RE = re.compile(
    r"(?i)(bearer\s+|authorization:\s*|ya29\.|api[_-]?key=|key=|access_token=)[^\s,&\"']+"
)


def _redact_secrets_in_text(text: str) -> str:
    """Strip secret query/header values from a copy of ``text`` for logs and exceptions.

    Matches ``key=``, ``api_key=``, and ``access_token=`` (plus bearer tokens). The
    original request URL is not mutated — callers must pass the live URL to
    ``session.request`` and redact only the strings they store or raise.
    """
    return _SECRET_IN_TEXT_RE.sub(r"\1[redacted]", text)


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


@dataclass(frozen=True)
class GoogleHttpErrorInfo:
    """Safe, loggable slice of a Google JSON error. Never includes tokens or headers."""

    status: int | None
    google_status: str
    reason: str
    message: str

    def short_description(self) -> str:
        parts: list[str] = []
        if self.status is not None:
            parts.append(f"HTTP {self.status}")
        else:
            parts.append("HTTP error")
        if self.google_status:
            parts.append(self.google_status)
        if self.reason:
            parts.append(self.reason)
        if self.message:
            parts.append(self.message)
        return " ".join(parts)


def _safe_truncate(text: str, cap: int = _GOOGLE_ERROR_MESSAGE_CAP) -> str:
    cleaned = _redact_secrets_in_text(text.replace("\n", " ").strip())
    if len(cleaned) <= cap:
        return cleaned
    return cleaned[:cap]


def describe_google_http_error(exc: HttpRequestError, *, message_cap: int = _GOOGLE_ERROR_MESSAGE_CAP) -> GoogleHttpErrorInfo:
    """HTTP status plus Google JSON ``error.status``, ``error.errors[0].reason``, truncated message.

    Never reads Authorization headers, request bodies, or keys. Non-JSON / empty bodies
    yield status only.
    """
    google_status = ""
    reason = ""
    message = ""
    body = exc.body if isinstance(exc.body, str) else ""
    if body:
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                raw_status = err.get("status")
                if isinstance(raw_status, str):
                    google_status = raw_status.strip()
                raw_message = err.get("message")
                if isinstance(raw_message, str) and raw_message.strip():
                    message = raw_message.strip()
                errors = err.get("errors")
                if isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        raw_reason = first.get("reason")
                        if isinstance(raw_reason, str):
                            reason = raw_reason.strip()
                        if not message:
                            nested_msg = first.get("message")
                            if isinstance(nested_msg, str) and nested_msg.strip():
                                message = nested_msg.strip()
            elif isinstance(err, str) and err.strip():
                message = err.strip()
    return GoogleHttpErrorInfo(
        status=exc.status,
        google_status=google_status,
        reason=reason,
        message=_safe_truncate(message, message_cap) if message else "",
    )


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
            raise HttpRequestError(
                _redact_secrets_in_text(f"HTTP unknown for {url}"),
                reason=_redact_secrets_in_text(str(exc)),
            ) from exc
        raise HttpRequestError(
            _redact_secrets_in_text(f"HTTP {response.status_code} for {url}"),
            status=response.status_code,
            body=response.text,
            reason=_redact_secrets_in_text(str(exc)),
            headers=dict(response.headers),
        ) from exc
    except requests.RequestException as exc:
        raise HttpRequestError(
            _redact_secrets_in_text(f"Connection error for {url}: {exc}"),
            reason=_redact_secrets_in_text(str(exc)),
        ) from exc


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
