"""Shared HTTP fetching with retries, backoff, and helpful errors."""

from __future__ import annotations

import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_MAX_BACKOFF = 30.0

HTTP_403_MESSAGE = (
    "Request blocked with HTTP 403. This source may require browser-like access "
    "from your network. Try: (1) run with --input-html using a saved copy of the "
    "page, (2) run from a different network, or (3) pass a different --user-agent."
)


def fetch_url(
    candidates: list[str],
    *,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 0.5,
    user_agent: str | None = None,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> str:
    """Fetch the first candidate URL that succeeds.

    Each candidate is tried in order; transient failures (timeouts, connection
    errors, HTTP 5xx, protocol errors) are retried with exponential backoff
    (capped at 30s). HTTP 403 raises an actionable error; other 4xx are not
    retried and fall through to the next candidate.
    """
    if not candidates:
        raise ValueError("At least one candidate URL must be provided.")

    request_headers = {"User-Agent": user_agent or DEFAULT_USER_AGENT, **DEFAULT_HEADERS}
    if headers:
        request_headers.update(headers)

    attempts = max(1, retries)
    last_error: Exception | None = None
    for url in candidates:
        for attempt in range(attempts):
            request = Request(url, data=data, headers=request_headers)
            try:
                # urlopen targets a vetted HTTP(S) source URL, the intended use.
                with urlopen(request, timeout=timeout) as response:  # nosec B310
                    charset = response.headers.get_content_charset() or "utf-8"
                    body: bytes = response.read()
                    try:
                        return body.decode(charset, errors="replace")
                    except LookupError:
                        return body.decode("utf-8", errors="replace")
            except HTTPError as exc:
                last_error = exc
                if exc.code == 403 or not (500 <= exc.code < 600):
                    break  # 403/other 4xx: do not retry; try the next candidate
            except (URLError, TimeoutError, HTTPException) as exc:
                last_error = exc
            if attempt < attempts - 1:
                time.sleep(min(backoff * (2**attempt), _MAX_BACKOFF))

    if isinstance(last_error, HTTPError) and last_error.code == 403:
        raise RuntimeError(HTTP_403_MESSAGE) from last_error
    raise RuntimeError(f"Failed to fetch source page: {last_error}") from last_error
