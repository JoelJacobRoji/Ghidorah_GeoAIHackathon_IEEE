"""Shared HTTP client with timeouts, bounded retries and exponential backoff."""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from ..config import get_settings
from ..log import mask

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """Raised when an upstream provider fails. Messages are secret-masked."""

    def __init__(self, provider: str, message: str, status: int | None = None):
        super().__init__(mask(f"{provider}: {message}"))
        self.provider = provider
        self.status = status


def make_client(timeout: float | None = None) -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        timeout=httpx.Timeout(timeout or s.http_timeout_s, connect=15.0),
        headers={"User-Agent": s.user_agent},
        follow_redirects=True,
    )


def request_with_retry(
    client: httpx.Client,
    provider: str,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            log.warning("%s: network error on attempt %d/%d: %s", provider, attempt, attempts, mask(type(exc).__name__))
        else:
            if resp.status_code not in RETRY_STATUS:
                return resp
            last_exc = ProviderError(provider, f"HTTP {resp.status_code}", resp.status_code)
            log.warning("%s: HTTP %d on attempt %d/%d", provider, resp.status_code, attempt, attempts)
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit() and attempt < attempts:
                time.sleep(min(int(retry_after), 90))
                continue
        if attempt < attempts:
            time.sleep(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
    if isinstance(last_exc, ProviderError):
        raise last_exc
    raise ProviderError(provider, f"request failed after {attempts} attempts ({type(last_exc).__name__})")
