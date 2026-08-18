"""
Prometheus metrics definitions.

All application metrics live here.  Import `metrics` and call methods on it.
The /metrics endpoint is served by the FastAPI app via prometheus_client.
"""
from dataclasses import dataclass, field
from typing import Dict

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


@dataclass
class AppMetrics:
    """
    Central container for all Prometheus metrics.
    Instantiated once at module level; imported everywhere.
    """

    # ---- Scraper metrics ----
    scraper_jobs_total: Counter = field(
        default_factory=lambda: Counter(
            "scraper_jobs_total",
            "Total number of job listings discovered",
            labelnames=["source", "status"],  # status: new | duplicate | error
        )
    )

    scraper_duration_seconds: Histogram = field(
        default_factory=lambda: Histogram(
            "scraper_duration_seconds",
            "Time taken to complete a full scrape run",
            labelnames=["source"],
            buckets=(1, 5, 10, 30, 60, 120, 300, 600),
        )
    )

    scraper_errors_total: Counter = field(
        default_factory=lambda: Counter(
            "scraper_errors_total",
            "Total scraper errors by type",
            labelnames=["source", "error_type"],
            # error_type: timeout | http_error | parse_error | unknown
        )
    )

    scraper_runs_total: Counter = field(
        default_factory=lambda: Counter(
            "scraper_runs_total",
            "Total number of scrape runs attempted",
            labelnames=["source", "status"],  # status: success | failed | partial
        )
    )

    # ---- API metrics ----
    api_requests_total: Counter = field(
        default_factory=lambda: Counter(
            "api_requests_total",
            "Total API requests",
            labelnames=["endpoint", "method", "status_code"],
        )
    )

    api_request_duration_seconds: Histogram = field(
        default_factory=lambda: Histogram(
            "api_request_duration_seconds",
            "API request processing time",
            labelnames=["endpoint", "method"],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
        )
    )

    # ---- Database metrics ----
    db_operations_total: Counter = field(
        default_factory=lambda: Counter(
            "db_operations_total",
            "Total database operations",
            labelnames=["operation", "status"],  # operation: insert|select|update
        )
    )

    # ---- Active gauges ----
    active_jobs_gauge: Gauge = field(
        default_factory=lambda: Gauge(
            "active_jobs_total",
            "Current total number of job listings in the database",
        )
    )

    scheduler_next_run_timestamp: Gauge = field(
        default_factory=lambda: Gauge(
            "scheduler_next_run_timestamp_seconds",
            "Unix timestamp of the next scheduled scrape run",
        )
    )

    # ---- Rate limiter metrics ----
    rate_limiter_waits_total: Counter = field(
        default_factory=lambda: Counter(
            "rate_limiter_waits_total",
            "Number of times the rate limiter caused a delay",
            labelnames=["domain"],
        )
    )

    rate_limiter_backoffs_total: Counter = field(
        default_factory=lambda: Counter(
            "rate_limiter_backoffs_total",
            "Number of exponential backoff events triggered",
            labelnames=["domain", "status_code"],
        )
    )

    def record_scrape_job(self, source: str, status: str) -> None:
        """Increment job counter (status: new|duplicate|error)."""
        self.scraper_jobs_total.labels(source=source, status=status).inc()

    def record_scrape_error(self, source: str, error_type: str) -> None:
        """Increment error counter."""
        self.scraper_errors_total.labels(source=source, error_type=error_type).inc()

    def record_scrape_run(self, source: str, status: str) -> None:
        """Increment run counter (status: success|failed|partial)."""
        self.scraper_runs_total.labels(source=source, status=status).inc()

    def record_api_request(
        self, endpoint: str, method: str, status_code: int
    ) -> None:
        """Increment API request counter."""
        self.api_requests_total.labels(
            endpoint=endpoint, method=method, status_code=str(status_code)
        ).inc()

    def set_active_jobs(self, count: int) -> None:
        """Update active jobs gauge."""
        self.active_jobs_gauge.set(count)

    def get_metrics_output(self) -> tuple[bytes, str]:
        """Return raw Prometheus metrics bytes and content-type."""
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


# Module-level singleton
metrics = AppMetrics()
