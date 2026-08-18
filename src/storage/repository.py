"""
Repository pattern for database access.

Provides clean, tested interfaces for all DB operations.
No raw SQL leaks into business logic.
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.monitoring.logger import get_logger
from src.monitoring.metrics import metrics
from src.storage.models import JobListing, ScrapeRun

log = get_logger(__name__)


class JobRepository:
    """Data access layer for JobListing records."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, job_id: str) -> Optional[JobListing]:
        """Fetch a single job by primary key."""
        result = await self._session.execute(
            select(JobListing).where(JobListing.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_by_hash(self, hash_value: str) -> Optional[JobListing]:
        """Check for an existing job by dedup hash."""
        result = await self._session.execute(
            select(JobListing).where(JobListing.hash == hash_value)
        )
        return result.scalar_one_or_none()

    async def exists_by_hash(self, hash_value: str) -> bool:
        """Fast existence check by hash (no full row load)."""
        result = await self._session.execute(
            select(JobListing.id).where(JobListing.hash == hash_value).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def create(self, job: JobListing) -> JobListing:
        """Persist a new job listing."""
        try:
            self._session.add(job)
            await self._session.flush()
            metrics.db_operations_total.labels(operation="insert", status="success").inc()
            log.debug("job_created", job_id=job.id, title=job.title[:50])
            return job
        except Exception as exc:
            metrics.db_operations_total.labels(operation="insert", status="error").inc()
            raise

    async def bulk_create(self, jobs: List[JobListing]) -> int:
        """
        Persist multiple job listings, skipping duplicates.
        Returns count of actually inserted rows.
        """
        inserted = 0
        for job in jobs:
            existing = await self.exists_by_hash(job.hash)
            if not existing:
                self._session.add(job)
                inserted += 1
        if inserted:
            await self._session.flush()
        return inserted

    async def list_jobs(
        self,
        *,
        source: Optional[str] = None,
        location: Optional[str] = None,
        keyword: Optional[str] = None,
        remote_only: Optional[bool] = None,
        salary_min: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
        order_by: str = "scraped_at_desc",
    ) -> Tuple[List[JobListing], int]:
        """
        Paginated, filterable job listing query.

        Returns:
            (list of jobs, total count matching filters)
        """
        query = select(JobListing)
        count_query = select(func.count(JobListing.id))

        # Apply filters
        filters = []
        if source:
            filters.append(JobListing.source == source)
        if location:
            filters.append(JobListing.location.ilike(f"%{location}%"))
        if remote_only is True:
            filters.append(JobListing.remote == True)  # noqa: E712
        if salary_min is not None:
            filters.append(
                or_(
                    JobListing.salary_min >= salary_min,
                    JobListing.salary_max >= salary_min,
                )
            )
        if keyword:
            kw = f"%{keyword}%"
            filters.append(
                or_(
                    JobListing.title.ilike(kw),
                    JobListing.company.ilike(kw),
                    JobListing.description.ilike(kw),
                )
            )

        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))

        # Ordering
        if order_by == "scraped_at_desc":
            query = query.order_by(JobListing.scraped_at.desc())
        elif order_by == "scraped_at_asc":
            query = query.order_by(JobListing.scraped_at.asc())
        elif order_by == "salary_desc":
            query = query.order_by(JobListing.salary_max.desc().nulls_last())
        elif order_by == "title_asc":
            query = query.order_by(JobListing.title.asc())
        else:
            query = query.order_by(JobListing.scraped_at.desc())

        # Pagination
        query = query.offset(offset).limit(limit)

        total_result = await self._session.execute(count_query)
        total = total_result.scalar_one()

        results = await self._session.execute(query)
        jobs = list(results.scalars().all())

        metrics.db_operations_total.labels(operation="select", status="success").inc()
        return jobs, total

    async def count_all(self) -> int:
        """Return total number of job listings."""
        result = await self._session.execute(select(func.count(JobListing.id)))
        return result.scalar_one()

    async def count_by_source(self) -> Dict[str, int]:
        """Return job counts grouped by source."""
        result = await self._session.execute(
            select(JobListing.source, func.count(JobListing.id)).group_by(
                JobListing.source
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def delete_old_jobs(self, before: datetime) -> int:
        """Delete jobs scraped before the given datetime. Returns count deleted."""
        result = await self._session.execute(
            delete(JobListing).where(JobListing.scraped_at < before)
        )
        count = result.rowcount
        if count:
            log.info("old_jobs_deleted", count=count, before=before.isoformat())
        return count


class ScrapeRunRepository:
    """Data access layer for ScrapeRun records."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, run: ScrapeRun) -> ScrapeRun:
        """Persist a new scrape run."""
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_by_id(self, run_id: str) -> Optional[ScrapeRun]:
        """Fetch a scrape run by ID."""
        result = await self._session.execute(
            select(ScrapeRun).where(ScrapeRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def update_finished(
        self,
        run_id: str,
        status: str,
        jobs_found: int,
        jobs_new: int,
        jobs_duplicate: int,
        jobs_error: int = 0,
        error_message: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Mark a scrape run as finished with stats."""
        await self._session.execute(
            update(ScrapeRun)
            .where(ScrapeRun.id == run_id)
            .values(
                finished_at=datetime.utcnow(),
                status=status,
                jobs_found=jobs_found,
                jobs_new=jobs_new,
                jobs_duplicate=jobs_duplicate,
                jobs_error=jobs_error,
                error_message=error_message,
                duration_seconds=int(duration_seconds) if duration_seconds else None,
            )
        )

    async def get_recent(self, limit: int = 10) -> List[ScrapeRun]:
        """Return the most recent scrape runs."""
        result = await self._session.execute(
            select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_last_by_source(self, source: str) -> Optional[ScrapeRun]:
        """Return the most recent run for a source."""
        result = await self._session.execute(
            select(ScrapeRun)
            .where(ScrapeRun.source == source)
            .order_by(ScrapeRun.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_stats_by_source(self) -> List[Dict]:
        """Return aggregated stats per source."""
        result = await self._session.execute(
            select(
                ScrapeRun.source,
                func.count(ScrapeRun.id).label("total_runs"),
                func.sum(ScrapeRun.jobs_found).label("total_jobs_found"),
                func.sum(ScrapeRun.jobs_new).label("total_jobs_new"),
                func.max(ScrapeRun.started_at).label("last_run"),
            ).group_by(ScrapeRun.source)
        )
        return [
            {
                "source": row.source,
                "total_runs": row.total_runs,
                "total_jobs_found": row.total_jobs_found or 0,
                "total_jobs_new": row.total_jobs_new or 0,
                "last_run": row.last_run,
            }
            for row in result.all()
        ]
