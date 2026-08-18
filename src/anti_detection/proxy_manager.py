"""
Proxy pool management.

Features:
- Supports HTTP and SOCKS5 proxies
- Periodic health checks (auto-removes dead proxies)
- Per-proxy success/failure tracking
- Graceful degradation to direct connection when pool is empty
- Configurable via environment (PROXY_LIST)
"""
import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from src.monitoring.logger import get_logger

log = get_logger(__name__)

# Timeout for proxy health checks
_HEALTH_CHECK_TIMEOUT = 10.0
_HEALTH_CHECK_URL = "https://httpbin.org/ip"


@dataclass
class ProxyStats:
    """Tracking stats for a single proxy."""

    url: str
    successes: int = 0
    failures: int = 0
    last_used: float = field(default_factory=time.monotonic)
    last_checked: float = field(default_factory=time.monotonic)
    is_healthy: bool = True

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total > 0 else 0.5

    def record_success(self) -> None:
        self.successes += 1
        self.last_used = time.monotonic()

    def record_failure(self) -> None:
        self.failures += 1
        self.last_used = time.monotonic()
        # Mark unhealthy after 3 consecutive failures
        if self.failures > 0 and self.successes == 0 and self.failures >= 3:
            self.is_healthy = False


class ProxyManager:
    """
    Manages a pool of HTTP/SOCKS5 proxies.

    If no proxies are configured or all are dead, returns None (direct connection).
    """

    def __init__(self, proxy_urls: Optional[List[str]] = None):
        self._stats: Dict[str, ProxyStats] = {}
        self._lock = asyncio.Lock()

        if proxy_urls:
            for url in proxy_urls:
                url = url.strip()
                if url:
                    self._stats[url] = ProxyStats(url=url)
            log.info("proxy_manager_initialised", proxy_count=len(self._stats))
        else:
            log.info("proxy_manager_no_proxies", message="Running without proxy pool")

    @property
    def pool_size(self) -> int:
        return len(self._stats)

    @property
    def healthy_proxies(self) -> List[str]:
        return [url for url, s in self._stats.items() if s.is_healthy]

    async def get_proxy(self) -> Optional[str]:
        """
        Return a random healthy proxy URL, or None for direct connection.

        Weighted by success_rate so better proxies are preferred.
        """
        healthy = self.healthy_proxies
        if not healthy:
            log.debug("proxy_manager_direct_connection", reason="no healthy proxies")
            return None

        # Weighted selection by success rate
        weights = [self._stats[p].success_rate for p in healthy]
        # Normalize weights (avoid all-zero edge case)
        total = sum(weights)
        if total == 0:
            weights = [1.0] * len(healthy)
        else:
            weights = [w / total for w in weights]

        chosen = random.choices(healthy, weights=weights, k=1)[0]
        log.debug(
            "proxy_manager_selected",
            proxy=chosen,
            success_rate=round(self._stats[chosen].success_rate, 2),
        )
        return chosen

    async def record_success(self, proxy_url: str) -> None:
        """Record a successful request through this proxy."""
        async with self._lock:
            if proxy_url in self._stats:
                self._stats[proxy_url].record_success()
                self._stats[proxy_url].is_healthy = True

    async def record_failure(self, proxy_url: str) -> None:
        """Record a failed request through this proxy."""
        async with self._lock:
            if proxy_url in self._stats:
                self._stats[proxy_url].record_failure()
                log.warning(
                    "proxy_failure_recorded",
                    proxy=proxy_url,
                    failures=self._stats[proxy_url].failures,
                    is_healthy=self._stats[proxy_url].is_healthy,
                )

    async def check_health(self) -> Dict[str, bool]:
        """
        Run health checks on all proxies in parallel.
        Marks dead proxies as unhealthy.
        Returns dict of {proxy_url: is_healthy}.
        """
        if not self._stats:
            return {}

        log.info("proxy_health_check_start", count=len(self._stats))
        results: Dict[str, bool] = {}

        async def _check(proxy_url: str) -> None:
            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=_HEALTH_CHECK_TIMEOUT,
                    verify=False,
                ) as client:
                    resp = await client.get(_HEALTH_CHECK_URL)
                    is_ok = resp.status_code == 200
            except Exception as exc:
                log.debug("proxy_health_check_failed", proxy=proxy_url, error=str(exc))
                is_ok = False

            results[proxy_url] = is_ok
            async with self._lock:
                if proxy_url in self._stats:
                    self._stats[proxy_url].is_healthy = is_ok
                    self._stats[proxy_url].last_checked = time.monotonic()
                    if not is_ok:
                        self._stats[proxy_url].failures += 1

        await asyncio.gather(*[_check(url) for url in self._stats], return_exceptions=True)

        healthy_count = sum(1 for v in results.values() if v)
        log.info(
            "proxy_health_check_complete",
            total=len(results),
            healthy=healthy_count,
        )
        return results

    def remove_dead_proxies(self) -> int:
        """Remove proxies that have been marked unhealthy. Returns count removed."""
        dead = [url for url, s in self._stats.items() if not s.is_healthy]
        for url in dead:
            del self._stats[url]
            log.info("proxy_removed", proxy=url)
        return len(dead)

    def get_stats(self) -> Dict[str, dict]:
        """Return stats for all proxies."""
        return {
            url: {
                "successes": s.successes,
                "failures": s.failures,
                "success_rate": round(s.success_rate, 3),
                "is_healthy": s.is_healthy,
            }
            for url, s in self._stats.items()
        }
