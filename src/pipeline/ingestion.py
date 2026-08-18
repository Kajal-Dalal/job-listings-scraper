"""
Main ingestion pipeline.

Orchestrates:
1. Running multiple scrapers in parallel (with semaphore to cap concurrency)
2. Normalising raw job data
3. Deduplicating against existing records
4. Persisting new jobs to the database
5. Recording scrape run metadata

Usage:
    pipeline = IngestionPipeline(database=db)
    result = await pipeline.run()
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from src.config.settings import get_settings
from src.monitoring.logger import get_logger
from src.monitoring.metrics import metrics
from src.pipeline.deduplicator import Deduplicator
from src.pipeline.normalizer import Normalizer
from src.scrapers.base_scraper import BaseScraper, ScraperResult
from src.scrapers.hn_jobs_scraper import HNJobsScraper
from src.scrapers.indeed_public import IndeedRssScraper
from src.scrapers.remoteok_scraper import RemoteOKScraper
from src.storage.database import Database
from src.storage.models import ScrapeRun
from src.storage.repository import JobRepository, ScrapeRunRepository
from src.utils.events import IngestionCompletedEvent, NewJobsFoundEvent, event_bus

log = get_logger(__name__)


@dataclass
class IngestionResult:
    """Summary of a full ingestion pipeline run."""

    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    total_found: int = 0
    total_new: int = 0
    total_duplicate: int = 0
    total_error: int = 0
    sources_run: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    success: bool = False

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


def build_scrapers(settings=None) -> List[BaseScraper]:
    """
    Build the list of scrapers based on configured enabled sources.
    """
    if settings is None:
        settings = get_settings()

    enabled = settings.enabled_sources_list
    scrapers: List[BaseScraper] = []

    if "remoteok" in enabled:
        scrapers.append(
            RemoteOKScraper(max_results=settings.remoteok_max_results)
        )

    if "hn_jobs" in enabled:
        scrapers.append(
            HNJobsScraper(max_results=settings.hn_jobs_max_results)
        )

    if "indeed_rss" in enabled:
        scrapers.append(
            IndeedRssScraper(
                query=settings.indeed_default_query,
                location=settings.indeed_default_location,
            )
        )

    log.info("scrapers_built", count=len(scrapers), sources=enabled)
    return scrapers


class IngestionPipeline:
    """
    Orchestrates the full scrape → normalise → dedup → persist pipeline.
    """

    def __init__(
        self,
        database: Database,
        scrapers: Optional[List[BaseScraper]] = None,
        max_concurrent: int = 3,
    ):
        self._db = database
        self._scrapers = scrapers or build_scrapers()
        self._max_concurrent = max_concurrent
        self._normalizer = Normalizer()
        self._deduplicator = Deduplicator()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def run(self) -> IngestionResult:
        """
        Run all scrapers in parallel, persist results.

        Returns:
            IngestionResult with totals and status.
        """
        result = IngestionResult()
        start_time = time.monotonic()

        log.info(
            "ingestion_pipeline_start",
            scraper_count=len(self._scrapers),
            max_concurrent=self._max_concurrent,
        )

        # Run all scrapers concurrently (bounded by semaphore)
        scrape_results: List[ScraperResult] = await asyncio.gather(
            *[self._run_single_scraper(s) for s in self._scrapers],
            return_exceptions=False,
        )

        # Process results
        for scrape_result in scrape_results:
            result.sources_run.append(scrape_result.source)
            result.errors.extend(scrape_result.errors)
            result.total_error += len(scrape_result.errors)

            if not scrape_result.success and not scrape_result.jobs:
                continue

            # Normalise raw jobs
            normalised = self._normalizer.normalize_many(scrape_result.jobs)
            result.total_found += len(normalised)

            # Persist to database
            new_count, dup_count = await self._persist_jobs(
                source=scrape_result.source,
                jobs=normalised,
                scrape_result=scrape_result,
            )
            result.total_new += new_count
            result.total_duplicate += dup_count

        result.finished_at = datetime.utcnow()
        result.success = True  # Partial success still counts

        # Update active jobs gauge
        try:
            async with self._db.session() as session:
                repo = JobRepository(session)
                total = await repo.count_all()
                metrics.set_active_jobs(total)
        except Exception as exc:
            log.warning("ingestion_gauge_update_failed", error=str(exc))

        # Persist bloom filter
        self._deduplicator.persist()
        self._deduplicator.reset_session()

        elapsed = time.monotonic() - start_time

        # Publish IngestionCompletedEvent (FreshMart pattern: pipeline end → event)
        await event_bus.publish(IngestionCompletedEvent(
            total_found=result.total_found,
            total_new=result.total_new,
            total_duplicate=result.total_duplicate,
            total_error=result.total_error,
            sources_run=tuple(result.sources_run),
            duration_seconds=round(elapsed, 2),
        ))

        log.info(
            "ingestion_pipeline_complete",
            total_found=result.total_found,
            total_new=result.total_new,
            total_duplicate=result.total_duplicate,
            total_error=result.total_error,
            duration_seconds=round(elapsed, 2),
        )
        return result

    async def _run_single_scraper(self, scraper: BaseScraper) -> ScraperResult:
        """Run a single scraper respecting the concurrency semaphore."""
        async with self._semaphore:
            return await scraper.scrape()

    async def _persist_jobs(
        self,
        source: str,
        jobs: list,
        scrape_result: ScraperResult,
    ) -> tuple[int, int]:
        """
        Persist jobs to DB, recording a ScrapeRun for auditability.

        Returns: (new_count, duplicate_count)
        """
        new_count = 0
        dup_count = 0

        async with self._db.session() as session:
            job_repo = JobRepository(session)
            run_repo = ScrapeRunRepository(session)

            # Create ScrapeRun record
            run = ScrapeRun(
                source=source,
                started_at=scrape_result.started_at,
                status=ScrapeRun.STATUS_RUNNING,
            )
            run = await run_repo.create(run)

            try:
                # Filter duplicates
                new_jobs, dup_count = await self._deduplicator.filter_new_jobs(
                    jobs, job_repo
                )
                new_count = len(new_jobs)

                # Insert new jobs
                if new_jobs:
                    inserted = await job_repo.bulk_create(new_jobs)
                    new_count = inserted

                # Publish NewJobsFoundEvent if new jobs were persisted
                if new_count > 0:
                    total_in_db = await job_repo.count_all()
                    await event_bus.publish(NewJobsFoundEvent(
                        source=source,
                        count=new_count,
                        total_in_db=total_in_db,
                    ))

                # Record metrics
                for _ in range(new_count):
                    metrics.record_scrape_job(source, "new")
                for _ in range(dup_count):
                    metrics.record_scrape_job(source, "duplicate")

                # Update ScrapeRun
                duration = scrape_result.duration_seconds or 0
                status = (
                    ScrapeRun.STATUS_SUCCESS
                    if scrape_result.success
                    else ScrapeRun.STATUS_PARTIAL
                )
                await run_repo.update_finished(
                    run_id=run.id,
                    status=status,
                    jobs_found=len(jobs),
                    jobs_new=new_count,
                    jobs_duplicate=dup_count,
                    jobs_error=len(scrape_result.errors),
                    error_message="; ".join(scrape_result.errors[:3]) or None,
                    duration_seconds=duration,
                )

                log.info(
                    "persist_complete",
                    source=source,
                    new=new_count,
                    duplicates=dup_count,
                )

            except Exception as exc:
                log.error("persist_failed", source=source, error=str(exc))
                await run_repo.update_finished(
                    run_id=run.id,
                    status=ScrapeRun.STATUS_FAILED,
                    jobs_found=len(jobs),
                    jobs_new=0,
                    jobs_duplicate=0,
                    error_message=str(exc)[:500],
                )
                raise

        return new_count, dup_count
