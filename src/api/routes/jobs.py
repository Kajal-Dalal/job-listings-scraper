"""
Job listing API endpoints.

GET /api/v1/jobs              — paginated, filterable list (offset)
GET /api/v1/jobs/cursor       — cursor-based pagination (stable, for live data)
GET /api/v1/jobs/stats        — platform statistics (FreshMart admin dashboard pattern)
GET /api/v1/jobs/{job_id}     — single job detail
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    CursorPaginatedJobsResponse,
    JobFilters,
    JobListingSchema,
    JobListingSummarySchema,
    PaginatedJobsResponse,
    StatsResponse,
)
from src.monitoring.logger import get_logger
from src.storage.database import get_db_session
from src.storage.models import JobListing, ScrapeRun
from src.storage.repository import JobRepository, ScrapeRunRepository
from src.utils.pagination import decode_cursor, encode_cursor

log = get_logger(__name__)
router = APIRouter()


@router.get(
    "/jobs",
    response_model=PaginatedJobsResponse,
    summary="List Job Listings",
    description=(
        "Returns paginated job listings with optional filtering by source, "
        "location, keyword, remote status, and minimum salary."
    ),
    tags=["jobs"],
)
async def list_jobs(
    source: Optional[str] = Query(None, description="Filter by source (e.g. remoteok, hn_jobs)"),
    location: Optional[str] = Query(None, description="Partial location match"),
    keyword: Optional[str] = Query(None, description="Search title/company/description"),
    remote_only: Optional[bool] = Query(None, description="Show only remote jobs"),
    salary_min: Optional[int] = Query(None, ge=0, description="Minimum salary filter"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    order_by: str = Query(
        "scraped_at_desc",
        description="Sort: scraped_at_desc|scraped_at_asc|salary_desc|title_asc",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedJobsResponse:
    """List and filter job listings with pagination."""
    repo = JobRepository(session)
    offset = (page - 1) * page_size

    jobs, total = await repo.list_jobs(
        source=source,
        location=location,
        keyword=keyword,
        remote_only=remote_only,
        salary_min=salary_min,
        limit=page_size,
        offset=offset,
        order_by=order_by,
    )

    total_pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedJobsResponse(
        items=[JobListingSummarySchema.model_validate(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
    )


@router.get(
    "/jobs/stats",
    response_model=StatsResponse,
    summary="Platform Statistics",
    description=(
        "Returns aggregate statistics — total jobs, per-source breakdown, "
        "remote count, and scrape run history. "
        "Inspired by FreshMart's admin dashboard metrics endpoint."
    ),
    tags=["jobs"],
)
async def get_stats(
    session: AsyncSession = Depends(get_db_session),
) -> StatsResponse:
    """Platform-wide statistics for dashboards and monitoring."""
    job_repo = JobRepository(session)
    run_repo = ScrapeRunRepository(session)

    total_jobs = await job_repo.count_all()
    jobs_by_source = await job_repo.count_by_source()

    # Count remote jobs
    remote_result = await session.execute(
        select(func.count(JobListing.id)).where(JobListing.remote == True)  # noqa: E712
    )
    remote_jobs = remote_result.scalar_one()

    # Total scrape runs
    runs_result = await session.execute(select(func.count(ScrapeRun.id)))
    total_runs = runs_result.scalar_one()

    # Last scrape timestamp
    recent_runs = await run_repo.get_recent(limit=1)
    last_scrape = recent_runs[0].started_at.isoformat() if recent_runs else None

    # Source stats
    sources_stats = await run_repo.get_stats_by_source()

    return StatsResponse(
        total_jobs=total_jobs,
        jobs_by_source=jobs_by_source,
        remote_jobs=remote_jobs,
        total_scrape_runs=total_runs,
        last_scrape_at=last_scrape,
        sources_status=[
            {
                "source": s["source"],
                "total_runs": s["total_runs"],
                "total_jobs_found": s["total_jobs_found"],
                "total_jobs_new": s["total_jobs_new"],
                "last_run": s["last_run"].isoformat() if s["last_run"] else None,
            }
            for s in sources_stats
        ],
    )


@router.get(
    "/jobs/cursor",
    response_model=CursorPaginatedJobsResponse,
    summary="Cursor-Paginated Job Listings",
    description=(
        "Stable cursor-based pagination for live data. "
        "Unlike offset pagination, this won't skip or repeat jobs when new data arrives. "
        "Pass `next_cursor` from the response as `?cursor=` for the next page."
    ),
    tags=["jobs"],
)
async def list_jobs_cursor(
    cursor: Optional[str] = Query(None, description="Pagination cursor from previous response"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    source: Optional[str] = Query(None),
    remote_only: Optional[bool] = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> CursorPaginatedJobsResponse:
    """Cursor-based paginated job listings — stable for live-updating data."""
    from sqlalchemy import and_, or_

    query = select(JobListing)
    count_query = select(func.count(JobListing.id))

    filters = []
    if source:
        filters.append(JobListing.source == source)
    if remote_only is True:
        filters.append(JobListing.remote == True)  # noqa: E712

    # Apply cursor filter
    if cursor:
        try:
            cursor_ts, cursor_id = decode_cursor(cursor)
            # Get items strictly before the cursor position (older than cursor)
            filters.append(
                or_(
                    JobListing.scraped_at < cursor_ts,
                    and_(
                        JobListing.scraped_at == cursor_ts,
                        JobListing.id < cursor_id,
                    ),
                )
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid pagination cursor",
            )

    if filters:
        from sqlalchemy import and_ as and2
        query = query.where(and2(*filters))
        count_query = count_query.where(and2(*filters))

    query = query.order_by(
        JobListing.scraped_at.desc(), JobListing.id.desc()
    ).limit(page_size + 1)  # Fetch one extra to detect has_next

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    results = await session.execute(query)
    jobs = list(results.scalars().all())

    has_next = len(jobs) > page_size
    if has_next:
        jobs = jobs[:page_size]

    next_cursor = None
    if has_next and jobs:
        last = jobs[-1]
        next_cursor = encode_cursor(last.scraped_at, last.id)

    return CursorPaginatedJobsResponse(
        items=[JobListingSummarySchema.model_validate(j) for j in jobs],
        total=total,
        has_next=has_next,
        has_prev=cursor is not None,
        next_cursor=next_cursor,
        page_size=page_size,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobListingSchema,
    summary="Get Job Detail",
    description="Returns the full details of a single job listing by ID.",
    tags=["jobs"],
    responses={
        404: {"description": "Job not found"},
    },
)
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> JobListingSchema:
    """Fetch a single job listing by its ID."""
    repo = JobRepository(session)
    job = await repo.get_by_id(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with id '{job_id}' not found",
        )

    return JobListingSchema.model_validate(job)
