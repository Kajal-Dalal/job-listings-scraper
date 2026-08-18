"""
Abstract base class for all scrapers.

All concrete scrapers must inherit BaseScraper and implement `_fetch_jobs()`.

Built-in features:
- Automatic rate limiting via DomainRateLimiter
- Retry with tenacity on transient errors
- Error classification (transient vs permanent)
- Prometheus metrics emission
- Structured logging
- Session management
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from src.anti_detection.rate_limiter import DomainRateLimiter
from src.anti_detection.session_manager import SessionManager
from src.anti_detection.user_agent_rotator import UserAgentRotator
from src.monitoring.logger import get_logger
from src.monitoring.metrics import metrics
from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, get_circuit_breaker
from src.utils.events import ScrapeCompletedEvent, event_bus
from src.utils.retry import RetryConfig, is_retryable_exception

log = get_logger(__name__)


@dataclass
class RawJobData:
    """
    Minimally processed job data returned by a scraper.
    Normalisation to the final schema happens in the pipeline.
    """

    source: str
    external_id: Optional[str]
    title: str
    company: str
    location: Optional[str]
    url: str
    description: Optional[str]
    salary_raw: Optional[str]
    tags: List[str] = field(default_factory=list)
    posted_at_raw: Optional[str] = None
    remote: Optional[bool] = None
    extra: Dict = field(default_factory=dict)


@dataclass
class ScraperResult:
    """Summary of a single scraper invocation."""

    source: str
    jobs: List[RawJobData] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    success: bool = False

    @property
    def job_count(self) -> int:
        return len(self.jobs)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class BaseScraper(ABC):
    """
    Abstract base for all job scrapers.

    Subclasses implement `_fetch_jobs()` which returns a list of RawJobData.
    The `scrape()` method wraps this with retry, rate limiting, and metrics.
    """

    #: Name used in logs, metrics, and DB records. Override in subclass.
    source_name: str = "base"

    #: Default retry configuration. Override per scraper if needed.
    retry_config: RetryConfig = RetryConfig(max_attempts=3, min_wait_seconds=2.0)

    def __init__(
        self,
        rate_limiter: Optional[DomainRateLimiter] = None,
        session_manager: Optional[SessionManager] = None,
        ua_rotator: Optional[UserAgentRotator] = None,
    ):
        self._rate_limiter = rate_limiter or DomainRateLimiter(
            min_delay=2.0, max_delay=8.0
        )
        self._ua_rotator = ua_rotator or UserAgentRotator()
        self._session_manager = session_manager or SessionManager(
            ua_rotator=self._ua_rotator
        )
        # Circuit breaker per source — prevents hammering a blocked source
        self._circuit_breaker: CircuitBreaker = get_circuit_breaker(
            name=self.source_name,
            failure_threshold=3,
            recovery_timeout=120.0,
        )

    async def scrape(self) -> ScraperResult:
        """
        Execute a scrape run with full error handling, metrics, and logging.

        Returns ScraperResult regardless of success/failure.
        """
        result = ScraperResult(source=self.source_name)
        start_time = time.monotonic()

        log.info("scraper_started", source=self.source_name)

        try:
            with metrics.scraper_duration_seconds.labels(
                source=self.source_name
            ).time():
                jobs = await self._circuit_breaker.call(self._scrape_with_retry)

            result.jobs = jobs
            result.success = True
            result.finished_at = datetime.utcnow()

            metrics.record_scrape_run(self.source_name, "success")
            log.info(
                "scraper_finished",
                source=self.source_name,
                jobs_found=len(jobs),
                duration_seconds=round(time.monotonic() - start_time, 2),
            )

        except CircuitBreakerOpenError as exc:
            result.success = False
            result.finished_at = datetime.utcnow()
            result.errors.append(str(exc))
            metrics.record_scrape_error(self.source_name, "circuit_open")
            metrics.record_scrape_run(self.source_name, "failed")
            log.warning(
                "scraper_circuit_open",
                source=self.source_name,
                retry_after=exc.retry_after,
            )

        except Exception as exc:
            result.success = False
            result.finished_at = datetime.utcnow()
            error_type = _classify_error(exc)
            result.errors.append(str(exc))

            metrics.record_scrape_error(self.source_name, error_type)
            metrics.record_scrape_run(self.source_name, "failed")

            log.error(
                "scraper_failed",
                source=self.source_name,
                error_type=error_type,
                error=str(exc),
                duration_seconds=round(time.monotonic() - start_time, 2),
            )

        # Publish domain event (FreshMart pattern: every significant action → event)
        await event_bus.publish(ScrapeCompletedEvent(
            source=self.source_name,
            jobs_found=len(result.jobs),
            success=result.success,
            duration_seconds=round(time.monotonic() - start_time, 2),
            error=result.errors[0] if result.errors else None,
        ))

        return result

    async def _scrape_with_retry(self) -> List[RawJobData]:
        """
        Invoke `_fetch_jobs()` with tenacity retry for transient errors.
        """
        from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential_jitter, retry_if_exception

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.retry_config.max_attempts),
            wait=wait_exponential_jitter(
                initial=self.retry_config.min_wait_seconds,
                max=self.retry_config.max_wait_seconds,
                jitter=self.retry_config.jitter,
            ),
            retry=retry_if_exception(is_retryable_exception),
            reraise=True,
        ):
            with attempt:
                return await self._fetch_jobs()

        # unreachable but makes type checker happy
        return []

    @abstractmethod
    async def _fetch_jobs(self) -> List[RawJobData]:
        """
        Core scraping logic. Must be implemented by all subclasses.

        Should:
          1. Build and execute HTTP request(s)
          2. Parse the response
          3. Return list of RawJobData

        Should NOT:
          - Handle retry (that's done in `_scrape_with_retry`)
          - Commit to database
        """
        ...

    async def _get(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        extra_headers: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
    ) -> httpx.Response:
        """
        Perform an HTTP GET with rate limiting and anti-detection headers.
        """
        await self._rate_limiter.wait_for_url(url)

        identity = self._session_manager.create_identity()
        headers = identity.get_request_headers()
        if extra_headers:
            headers.update(extra_headers)

        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            http2=True,
            proxy=proxy_url,
        ) as client:
            response = await client.get(url)

        if response.status_code == 429:
            await self._rate_limiter.backoff_for_url(url, 429)
            raise httpx.HTTPStatusError(
                "Rate limited (429)", request=response.request, response=response
            )
        if response.status_code >= 500:
            raise httpx.HTTPStatusError(
                f"Server error {response.status_code}",
                request=response.request,
                response=response,
            )

        response.raise_for_status()
        return response

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} source={self.source_name!r}>"


def _classify_error(exc: Exception) -> str:
    """Map exception to a metrics-friendly error type string."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "rate_limited"
        if code >= 500:
            return "server_error"
        return f"http_{code}"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, (ValueError, KeyError, AttributeError)):
        return "parse_error"
    return "unknown"
