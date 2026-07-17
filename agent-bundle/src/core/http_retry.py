"""
http_retry.py
Shared async HTTP retry logic for ADO API clients.

Retries on 429 (rate limited) and 503 (service unavailable). Respects
the Retry-After header, capped at 60s. Falls back to exponential backoff
(1s, 2s, 4s) when the header is absent.
"""

from __future__ import annotations

import asyncio
import ssl as _ssl
from typing import Any, Final

import httpx

MAX_RETRIES: Final[int] = 3
MAX_WAIT: Final[float] = 60.0
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset((429, 503))
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


async def request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Execute an HTTP request with retry-after handling for rate limits."""
    import time as _time
    from core.trace import trace_dependency

    backoff: float = 1.0
    t0 = _time.perf_counter()
    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt == MAX_RETRIES:
                elapsed = round((_time.perf_counter() - t0) * 1000, 2)
                trace_dependency(
                    "http", url, f"{method} CONNECT_ERR",
                    duration_ms=elapsed,
                    success=False,
                    status_code=0,
                    metadata={"attempts": attempt + 1, "exhausted": True,
                              "error": type(exc).__name__},
                )
                raise
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_WAIT)
            continue

        last_exc = None
        if resp.status_code not in _RETRYABLE_STATUSES:
            elapsed = round((_time.perf_counter() - t0) * 1000, 2)
            trace_dependency(
                "http", url, f"{method} {resp.status_code}",
                duration_ms=elapsed,
                success=resp.status_code < 400,
                status_code=resp.status_code,
                metadata={"attempts": attempt + 1},
            )
            return resp
        if attempt == MAX_RETRIES:
            elapsed = round((_time.perf_counter() - t0) * 1000, 2)
            trace_dependency(
                "http", url, f"{method} {resp.status_code}",
                duration_ms=elapsed,
                success=False,
                status_code=resp.status_code,
                metadata={"attempts": attempt + 1, "exhausted": True},
            )
            resp.raise_for_status()
        wait: float = backoff
        retry_after: str | None = resp.headers.get("Retry-After")
        if retry_after:
            try:
                wait = min(float(retry_after), MAX_WAIT)
            except (ValueError, TypeError):
                pass
        await asyncio.sleep(wait)
        backoff = min(backoff * 2, MAX_WAIT)
    if last_exc:
        raise last_exc
    return resp  # unreachable; satisfies type checker


def ssl_exception_types() -> tuple[type[BaseException], ...]:
    """Return the SSL exception types to catch in API error handlers."""
    return (_ssl.SSLError, _ssl.SSLCertVerificationError)
