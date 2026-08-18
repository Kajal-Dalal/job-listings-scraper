"""
Retry configuration and helpers for tenacity.

Provides:
- RetryConfig dataclass for per-scraper retry settings
- is_retryable_exception() predicate used by tenacity
"""
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class RetryConfig:
    """
    Configuration for tenacity retry behaviour.

    Attributes:
        max_attempts:      Maximum total attempts (including the first).
        min_wait_seconds:  Initial wait before first retry.
        max_wait_seconds:  Maximum wait ceiling for exponential backoff.
        jitter:            Random jitter added to each wait (seconds).
    """

    max_attempts: int = 3
    min_wait_seconds: float = 2.0
    max_wait_seconds: float = 60.0
    jitter: float = 2.0


def is_retryable_exception(exc: BaseException) -> bool:
    """
    Predicate for tenacity: returns True if the exception is transient
    and worth retrying.

    Retryable:
    - Network timeouts
    - Connection errors (proxy down, DNS failure)
    - HTTP 429 Too Many Requests
    - HTTP 5xx Server Errors

    Not retryable:
    - HTTP 4xx (except 429) — client errors, won't change on retry
    - Parse errors / ValueError — data won't change on retry
    - Permanent failures
    """
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        # Retry on rate limit and all server errors
        return code == 429 or code >= 500
    # Don't retry parse errors, auth errors, etc.
    return False
