"""HTTP fetch with rich failure-mode classification.

The scraper's only network surface. All metric emission for fetches happens
HERE, not in runner.py — that means the runner gets a clean (status, html)
or an exception, and the metric story is contained to one file.

Failure-mode taxonomy (the `error_class` label on webharvest_fetch_errors_total):

  timeout              — httpx.TimeoutException (read or connect)
  dns                  — connection failed because hostname couldn't resolve
  connection_refused   — TCP refused (server down or wrong port)
  ssl                  — TLS handshake failed
  protocol             — invalid HTTP response (server speaks gibberish)
  http_4xx             — server returned 4xx (anti-bot, auth wall, etc.)
  http_5xx             — server returned 5xx (server error)
  anti_bot             — small response with anti-bot marker text
                         (cloudflare interstitial, captcha, etc.)
  other                — anything we didn't classify

Consecutive failure tracking lives in a module-level dict because the worker
is a long-lived process and we want the gauge to reflect "how many in a row"
without needing a database round-trip on every fetch.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .metrics import (
    fetch_bytes_total,
    fetch_consecutive_failures,
    fetch_errors_total,
    fetch_redirect_count,
    fetch_requests_total,
    fetch_response_size_bytes,
    fetch_status_total,
    fetch_total,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

DEFAULT_TIMEOUT = 20.0

ANTI_BOT_SIZE_THRESHOLD = 10_000
ANTI_BOT_MARKERS = (
    "cf-browser-verification",
    "cf-challenge",
    "cloudflare",
    "captcha",
    "are you a human",
    "verify you are human",
    "checking your browser",
    "access denied",
    "request blocked",
    "bot detection",
    "ddos protection",
)

_consecutive_failures: dict[str, int] = {}


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    html: str | None
    bytes_downloaded: int
    etag: str | None = None
    last_modified: str | None = None
    mode: str = "naive"
    result: str = "modified"


class FetchError(Exception):
    """Raised when fetch fails for any reason. error_class matches the metric
    label so runner.py can log it directly without re-classifying."""

    def __init__(self, error_class: str, message: str, status_code: int = 0):
        super().__init__(message)
        self.error_class = error_class
        self.status_code = status_code


def _classify_transport_error(exc: Exception) -> str:
    """Map a non-HTTP exception to one of our error_class label values."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        msg = str(exc).lower()
        if "name" in msg or "resolve" in msg or "nodename" in msg:
            return "dns"
        return "connection_refused"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "protocol"
    name = type(exc).__name__.lower()
    if "ssl" in name or "tls" in name or "certificate" in name:
        return "ssl"
    return "other"


def _looks_like_anti_bot(html: str) -> bool:
    """Two-factor heuristic: small response AND a challenge marker present."""
    if len(html) >= ANTI_BOT_SIZE_THRESHOLD:
        return False
    lo = html.lower()
    return any(marker in lo for marker in ANTI_BOT_MARKERS)


def _record_failure(sid: str, error_class: str) -> int:
    """Bump the consecutive-failure counter and update the gauge. Returns the
    new value so callers can include it in log output."""
    new_value = _consecutive_failures.get(sid, 0) + 1
    _consecutive_failures[sid] = new_value
    fetch_consecutive_failures.labels(source_id=sid).set(new_value)
    fetch_errors_total.labels(source_id=sid, error_class=error_class).inc()
    return new_value


def _record_success(sid: str) -> None:
    _consecutive_failures[sid] = 0
    fetch_consecutive_failures.labels(source_id=sid).set(0)


async def fetch(
    url: str,
    source_id: int | str | None = None,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    conditional: bool = False,
) -> FetchResult:
    """Fetch a URL and classify the polling strategy result.

    Returns a FetchResult. `html` is None only for HTTP 304 Not Modified.

    Raises FetchError on transport failure (timeout/dns/etc), HTTP 4xx/5xx,
    or detected anti-bot interstitial. The error's `error_class` attribute
    matches the metric label.

    source_id is optional (None for one-off uses) but should be passed for
    any production polling so the per-source consecutive-failure gauge stays
    accurate.
    """
    sid = str(source_id) if source_id is not None else "unknown"
    mode = "conditional" if conditional and (etag or last_modified) else "naive"
    headers = dict(DEFAULT_HEADERS)
    if mode == "conditional":
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

    try:
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
        ) as client:
            resp = await client.get(url)
    except Exception as e:
        error_class = _classify_transport_error(e)
        fetch_status_total.labels(source_id=sid, status_code="0").inc()
        fetch_total.labels(source_id=sid, outcome="error").inc()
        fetch_requests_total.labels(source_id=sid, mode=mode, result="error").inc()
        consecutive = _record_failure(sid, error_class)
        raise FetchError(
            error_class,
            f"transport failure ({error_class}): {e} [consecutive={consecutive}]",
        ) from e

    status = resp.status_code
    html = resp.text
    bytes_downloaded = len(resp.content)
    fetch_status_total.labels(source_id=sid, status_code=str(status)).inc()
    fetch_response_size_bytes.labels(source_id=sid).observe(bytes_downloaded)
    fetch_redirect_count.labels(source_id=sid).observe(len(resp.history))

    if status == 304:
        fetch_total.labels(source_id=sid, outcome="ok").inc()
        fetch_requests_total.labels(
            source_id=sid, mode=mode, result="not_modified",
        ).inc()
        fetch_bytes_total.labels(source_id=sid, mode=mode).inc(0)
        _record_success(sid)
        return FetchResult(
            status_code=status,
            html=None,
            bytes_downloaded=0,
            etag=resp.headers.get("etag") or etag,
            last_modified=resp.headers.get("last-modified") or last_modified,
            mode=mode,
            result="not_modified",
        )

    if status >= 400:
        error_class = "http_4xx" if status < 500 else "http_5xx"
        fetch_total.labels(source_id=sid, outcome="error").inc()
        fetch_requests_total.labels(source_id=sid, mode=mode, result="error").inc()
        fetch_bytes_total.labels(source_id=sid, mode=mode).inc(bytes_downloaded)
        consecutive = _record_failure(sid, error_class)
        raise FetchError(
            error_class,
            f"HTTP {status} from {url} [consecutive={consecutive}]",
            status_code=status,
        )

    if _looks_like_anti_bot(html):
        fetch_total.labels(source_id=sid, outcome="error").inc()
        fetch_requests_total.labels(source_id=sid, mode=mode, result="error").inc()
        fetch_bytes_total.labels(source_id=sid, mode=mode).inc(bytes_downloaded)
        consecutive = _record_failure(sid, "anti_bot")
        raise FetchError(
            "anti_bot",
            f"Suspected anti-bot interstitial ({len(html)}B) from {url} "
            f"[consecutive={consecutive}]",
            status_code=status,
        )

    if "\x00" in html:
        html = html.replace("\x00", "")
    fetch_total.labels(source_id=sid, outcome="ok").inc()
    new_etag = resp.headers.get("etag")
    new_last_modified = resp.headers.get("last-modified")
    result = "modified" if (not conditional or new_etag or new_last_modified) else "unsupported"
    fetch_requests_total.labels(source_id=sid, mode=mode, result=result).inc()
    fetch_bytes_total.labels(source_id=sid, mode=mode).inc(bytes_downloaded)
    _record_success(sid)
    return FetchResult(
        status_code=status,
        html=html,
        bytes_downloaded=bytes_downloaded,
        etag=new_etag,
        last_modified=new_last_modified,
        mode=mode,
        result=result,
    )
