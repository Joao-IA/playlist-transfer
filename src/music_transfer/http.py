"""Retries, backoff and rate-limit handling shared by every provider."""

import time
from collections.abc import Callable
from typing import Any

import requests

RETRIES = 3
TIMEOUT = 30


class RetryableError(Exception):
    """For errors worth retrying that don't come as HTTP errors (e.g. a quota error sent with status 200)."""

    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


def _retry_wait(error: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None when retrying won't help."""
    if isinstance(error, requests.HTTPError) and error.response is not None:
        status = error.response.status_code
        if status != 429 and status < 500:
            return None  # bad request, expired login, not found: won't fix itself
        retry_after = error.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return int(retry_after) + 1
    if isinstance(error, RetryableError) and error.wait:
        return error.wait
    return 5 * attempt**2


def call(fn, *args, delay: float = 0.0, retry: bool = True, **kwargs):
    """Call `fn`, pause `delay` seconds afterwards to stay under rate limits, and retry on errors.

    Use retry=False for calls that must not run twice, like creating a playlist: if the request
    times out after the server did the work, a retry would create a duplicate."""
    attempts = RETRIES if retry else 1
    for attempt in range(1, attempts + 1):
        try:
            result = fn(*args, **kwargs)
            time.sleep(delay)
            return result
        except Exception as error:
            wait = _retry_wait(error, attempt)
            if attempt == attempts or wait is None:
                raise
            print(f"  ! error: {error} — retrying in {wait:g}s")
            time.sleep(wait)


class ApiClient:
    """Small JSON-over-HTTP client: base URL, default headers and params, throttling and retries."""

    def __init__(
        self,
        base_url: str,
        headers: dict | None = None,
        params: dict | None = None,
        delay: float = 0.0,
        check: Callable[[Any], None] | None = None,  # inspects each JSON body; may raise RetryableError
    ):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(headers or {})
        self.params = dict(params or {})
        self.delay = delay
        self.check = check

    def request(self, method: str, path: str, retry: bool = True, **kwargs) -> Any:
        return call(self._send, method, path, delay=self.delay, retry=retry, **kwargs)

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> Any:
        return self.request("PUT", path, **kwargs)

    def _send(self, method: str, path: str, params: dict | None = None, **kwargs) -> Any:
        url = path if path.startswith("http") else self.base_url + path
        params = {**self.params, **(params or {})}
        response = self.session.request(method, url, params=params, timeout=TIMEOUT, **kwargs)
        if not response.ok:
            # Error bodies explain what went wrong; the full URL is left out because it may carry tokens
            raise requests.HTTPError(
                f"{response.status_code} {response.reason} on {method} {path.split('?')[0]}: {response.text[:300]}",
                response=response,
            )
        data = response.json() if response.content else None
        if self.check:
            self.check(data)
        return data
