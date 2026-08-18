"""
Circuit Breaker pattern — inspired by FreshMart's PaymentServiceClient resilience design.

States:
  CLOSED   → normal operation, requests pass through
  OPEN     → too many failures, requests fail fast without hitting the source
  HALF_OPEN → testing if the source has recovered (allows one probe request)

Usage:
    cb = CircuitBreaker(name="remoteok", failure_threshold=3, recovery_timeout=60)

    @cb.call
    async def fetch():
        return await httpx.get(...)
"""
import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from src.monitoring.logger import get_logger
from src.monitoring.metrics import metrics

log = get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""
    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit '{name}' is OPEN. Retry after {retry_after:.1f}s."
        )


@dataclass
class CircuitBreaker:
    """
    Thread-safe async circuit breaker.

    Args:
        name:               Identifier for logging and metrics.
        failure_threshold:  Consecutive failures before opening the circuit.
        recovery_timeout:   Seconds to wait in OPEN state before trying HALF_OPEN.
        success_threshold:  Successes in HALF_OPEN needed to close the circuit.
    """
    name: str
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    success_threshold: int = 1

    # State (not init params)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    async def _check_state(self) -> None:
        """Transition OPEN → HALF_OPEN if recovery_timeout has elapsed."""
        async with self._lock:
            if (
                self._state == CircuitState.OPEN
                and time.monotonic() - self._last_failure_time >= self.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                log.info(
                    "circuit_breaker_half_open",
                    name=self.name,
                    recovery_timeout=self.recovery_timeout,
                )

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    log.info("circuit_breaker_closed", name=self.name)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — go back to OPEN
                self._state = CircuitState.OPEN
                log.warning("circuit_breaker_reopened", name=self.name)

            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                log.error(
                    "circuit_breaker_opened",
                    name=self.name,
                    failure_count=self._failure_count,
                    recovery_timeout=self.recovery_timeout,
                )

    async def call(self, fn: Callable, *args, **kwargs):
        """
        Execute `fn(*args, **kwargs)` through the circuit breaker.

        Raises:
            CircuitBreakerOpenError: if the circuit is OPEN.
            Any exception from `fn`: on failure (after recording it).
        """
        await self._check_state()

        if self._state == CircuitState.OPEN:
            retry_after = max(
                0.0,
                self.recovery_timeout - (time.monotonic() - self._last_failure_time),
            )
            metrics.record_scrape_error(self.name, "circuit_open")
            raise CircuitBreakerOpenError(self.name, retry_after)

        try:
            result = await fn(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure()
            raise

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "last_failure_age_seconds": (
                round(time.monotonic() - self._last_failure_time, 1)
                if self._last_failure_time
                else None
            ),
            "recovery_timeout": self.recovery_timeout,
        }


# ---------------------------------------------------------------------------
# Global registry — one breaker per scraper source
# ---------------------------------------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 3,
    recovery_timeout: float = 120.0,
) -> CircuitBreaker:
    """
    Get or create a named circuit breaker.
    All scrapers share the same registry so health endpoints can report all states.
    """
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
    return _breakers[name]


def get_all_breaker_statuses() -> list[dict]:
    """Return status of all registered circuit breakers."""
    return [cb.get_status() for cb in _breakers.values()]
