"""
APScheduler-based job scheduling.

Runs the ingestion pipeline on a configurable interval.
Provides methods to check the schedule status (used by /health endpoint).
"""
import asyncio
from datetime import datetime
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.monitoring.logger import get_logger
from src.monitoring.metrics import metrics

log = get_logger(__name__)


class Scheduler:
    """
    Wraps APScheduler to run the ingestion pipeline on a schedule.

    Thread-safe for use in an async FastAPI application.
    """

    JOB_ID = "ingestion_pipeline"

    def __init__(self, interval_minutes: int = 60):
        self._interval_minutes = interval_minutes
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._last_run: Optional[datetime] = None
        self._next_run: Optional[datetime] = None
        self._run_count: int = 0
        self._is_running: bool = False

    def start(self, pipeline_fn: Callable) -> None:
        """
        Start the scheduler with the given pipeline function.

        Args:
            pipeline_fn: Async callable that executes the ingestion pipeline.
                         Should be a zero-argument coroutine function.
        """
        if self._scheduler.running:
            log.warning("scheduler_already_running")
            return

        self._scheduler.add_job(
            self._wrapped_run(pipeline_fn),
            trigger=IntervalTrigger(minutes=self._interval_minutes),
            id=self.JOB_ID,
            name="Job Listings Ingestion",
            replace_existing=True,
            max_instances=1,  # Never run two instances simultaneously
        )

        self._scheduler.start()
        self._update_next_run()

        log.info(
            "scheduler_started",
            interval_minutes=self._interval_minutes,
            next_run=self._next_run.isoformat() if self._next_run else None,
        )

    def _wrapped_run(self, pipeline_fn: Callable) -> Callable:
        """Wrap pipeline_fn to update run stats and handle errors."""

        async def _run():
            self._is_running = True
            self._last_run = datetime.utcnow()
            log.info("scheduler_run_start")

            try:
                await pipeline_fn()
                self._run_count += 1
                log.info("scheduler_run_complete", run_count=self._run_count)
            except Exception as exc:
                log.error("scheduler_run_failed", error=str(exc))
            finally:
                self._is_running = False
                self._update_next_run()

        return _run

    async def trigger_now(self, pipeline_fn: Callable) -> None:
        """
        Manually trigger a scrape run immediately.
        Runs in the background without blocking the caller.
        """
        log.info("scheduler_manual_trigger")
        asyncio.create_task(self._wrapped_run(pipeline_fn)())

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler_stopped")

    def _update_next_run(self) -> None:
        """Update cached next-run timestamp from APScheduler."""
        try:
            job = self._scheduler.get_job(self.JOB_ID)
            if job and job.next_run_time:
                self._next_run = job.next_run_time.replace(tzinfo=None)
                # Update Prometheus gauge
                metrics.scheduler_next_run_timestamp.set(
                    self._next_run.timestamp()
                )
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        """True if a scrape run is currently in progress."""
        return self._is_running

    @property
    def last_run(self) -> Optional[datetime]:
        return self._last_run

    @property
    def next_run(self) -> Optional[datetime]:
        self._update_next_run()
        return self._next_run

    @property
    def run_count(self) -> int:
        return self._run_count

    def get_status(self) -> dict:
        """Return a dict of scheduler status for the health endpoint."""
        return {
            "enabled": True,
            "running": self._is_running,
            "interval_minutes": self._interval_minutes,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "run_count": self._run_count,
        }
