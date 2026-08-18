"""
Rate limiting implementation.

Features:
- Token bucket algorithm per domain
- Configurable per-domain limits
- Exponential backoff with jitter on 429/503
- Human-like timing: Gaussian-distributed delays between min_delay and max_delay
- Burst detection avoidance
"""
import asyncio
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.monitoring.logger import get_logger
from src.monitoring.metrics import metrics

log = get_logger(__name__)


@dataclass
class TokenBucket:
    """
    Single-domain token bucket.

    Tokens are refilled at `rate` per second up to `capacity`.
    Consuming a token means permission to make one request.
    """

    capacity: float = 5.0          # max burst size
    rate: float = 1.0              # refill rate (tokens/second)
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self):
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        added = elapsed * self.rate
        self.tokens = min(self.capacity, self.tokens + added)
        self.last_refill = now

    def consume(self, tokens: float = 1.0) -> float:
        """
        Consume tokens.  Returns seconds to wait before retrying if not enough
        tokens are available (0.0 means proceed immediately).
        """
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return 0.0
        # How long until enough tokens are available
        deficit = tokens - self.tokens
        return deficit / self.rate


class RateLimiter:
    """
    Per-domain async rate limiter using token buckets.

    Each domain gets its own TokenBucket.  Falls back to global defaults
    if no domain-specific config exists.
    """

    DEFAULT_CAPACITY = 5.0   # allow small bursts
    DEFAULT_RATE = 0.5       # 1 request every 2 seconds default

    def __init__(
        self,
        min_delay: float = 2.0,
        max_delay: float = 8.0,
        default_rate: float = DEFAULT_RATE,
        default_capacity: float = DEFAULT_CAPACITY,
    ):
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._default_rate = default_rate
        self._default_capacity = default_capacity
        self._buckets: Dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                capacity=self._default_capacity, rate=self._default_rate
            )
        )
        self._domain_config: Dict[str, Dict[str, float]] = {}

    def configure_domain(
        self, domain: str, rate: float, capacity: float
    ) -> None:
        """Override rate/capacity for a specific domain."""
        self._domain_config[domain] = {"rate": rate, "capacity": capacity}
        self._buckets[domain] = TokenBucket(capacity=capacity, rate=rate)
        log.debug(
            "rate_limiter_domain_configured",
            domain=domain,
            rate=rate,
            capacity=capacity,
        )

    async def wait(self, domain: str = "default") -> None:
        """
        Block until it is safe to make a request to `domain`.

        1. Consume a token from the domain bucket (blocks if needed)
        2. Add a human-like Gaussian random delay on top
        """
        bucket = self._buckets[domain]
        wait_seconds = bucket.consume()

        if wait_seconds > 0:
            metrics.rate_limiter_waits_total.labels(domain=domain).inc()
            log.debug(
                "rate_limiter_token_wait", domain=domain, wait_seconds=round(wait_seconds, 2)
            )
            await asyncio.sleep(wait_seconds)

        # Human-like delay: Gaussian distribution clamped to [min, max]
        mean = (self._min_delay + self._max_delay) / 2
        std = (self._max_delay - self._min_delay) / 4
        human_delay = random.gauss(mean, std)
        human_delay = max(self._min_delay, min(self._max_delay, human_delay))

        log.debug(
            "rate_limiter_human_delay", domain=domain, delay_seconds=round(human_delay, 2)
        )
        await asyncio.sleep(human_delay)

    async def backoff(
        self,
        domain: str,
        status_code: int,
        attempt: int = 0,
    ) -> None:
        """
        Exponential backoff after a 429/503 response.

        Args:
            domain:      Domain that returned the error
            status_code: HTTP status code (429, 503, etc.)
            attempt:     Current attempt number (0-indexed) for exponential calc
        """
        base_wait = min(2 ** (attempt + 1), 120)
        jitter = random.uniform(0, min(base_wait * 0.3, 30))
        total_wait = base_wait + jitter

        metrics.rate_limiter_backoffs_total.labels(
            domain=domain, status_code=str(status_code)
        ).inc()

        log.warning(
            "rate_limiter_backoff",
            domain=domain,
            status_code=status_code,
            attempt=attempt,
            wait_seconds=round(total_wait, 2),
        )
        await asyncio.sleep(total_wait)


class DomainRateLimiter:
    """
    Convenience class that wraps RateLimiter and auto-extracts domain from URL.
    """

    def __init__(self, **kwargs):
        self._limiter = RateLimiter(**kwargs)

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        try:
            return urlparse(url).netloc or "default"
        except Exception:
            return "default"

    async def wait_for_url(self, url: str) -> None:
        """Wait before making a request to the given URL."""
        domain = self._extract_domain(url)
        await self._limiter.wait(domain)

    async def backoff_for_url(
        self, url: str, status_code: int, attempt: int = 0
    ) -> None:
        """Backoff after a rate-limit/error response for the given URL."""
        domain = self._extract_domain(url)
        await self._limiter.backoff(domain, status_code, attempt)

    def configure(self, domain: str, rate: float, capacity: float) -> None:
        self._limiter.configure_domain(domain, rate, capacity)
