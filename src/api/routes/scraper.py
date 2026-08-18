"""
Scraper management endpoints.

POST /api/v1/scrape/trigger  — manually trigger a scrape (API key required)
GET  /api/v1/scrape/status   — last scrape stats
GET  /api/v1/sources         — list configured sources and their status
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    ScrapeTriggerResponse,
    SourceStatusSchema,
    SourcesResponse,
    ScrapeRunSchema,
)
from src.config.settings import get_settings
from src.monitoring.logger import get_logger
from src.storage.database import get_db_session
from src.storage.repository import ScrapeRunRepository

log = get_logger(__name__)
router = APIRouter()


async def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Dependency: validates X-API-Key header for protected endpoints."""
    settings = get_settings()
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return x_api_key


@router.post(
    "/scrape/trigger",
    response_model=ScrapeTriggerResponse,
    summary="Trigger Manual Scrape",
    description=(
        "Manually triggers a scrape run immediately. "
        "Requires X-API-Key header. Runs asynchronously in the background."
    ),
    tags=["scraper"],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"description": "Invalid or missing API key"},
    },
)
async def trigger_scrape(
    request: Request,
    _api_key: str = Depends(_require_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> ScrapeTriggerResponse:
    """Manually trigger a scrape run in the background."""
    settings = get_settings()

    # Get scheduler from app state
    scheduler = getattr(request.app.state, "scheduler", None)
    pipeline = getattr(request.app.state, "pipeline", None)

    if not scheduler or not pipeline:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler not available",
        )

    # Trigger in background
    await scheduler.trigger_now(pipeline.run)

    triggered_at = datetime.utcnow().isoformat()
    log.info("scrape_manually_triggered", triggered_at=triggered_at)

    return ScrapeTriggerResponse(
        message="Scrape run triggered successfully",
        triggered_at=triggered_at,
        sources=settings.enabled_sources_list,
    )


@router.get(
    "/scrape/status",
    response_model=List[ScrapeRunSchema],
    summary="Scrape Run History",
    description="Returns the 10 most recent scrape run records.",
    tags=["scraper"],
)
async def scrape_status(
    session: AsyncSession = Depends(get_db_session),
) -> List[ScrapeRunSchema]:
    """Return recent scrape run history."""
    repo = ScrapeRunRepository(session)
    runs = await repo.get_recent(limit=10)
    return [ScrapeRunSchema.model_validate(r) for r in runs]


@router.get(
    "/sources",
    response_model=SourcesResponse,
    summary="List Configured Sources",
    description="Returns all configured scraper sources with their status.",
    tags=["scraper"],
)
async def list_sources(
    session: AsyncSession = Depends(get_db_session),
) -> SourcesResponse:
    """List all configured sources and their run statistics."""
    settings = get_settings()
    repo = ScrapeRunRepository(session)

    # Get aggregated stats per source
    stats_rows = await repo.get_stats_by_source()
    stats_by_name = {row["source"]: row for row in stats_rows}

    # Get last run per source
    sources: List[SourceStatusSchema] = []
    for source_name in settings.enabled_sources_list:
        row = stats_by_name.get(source_name, {})
        last_run_obj = await repo.get_last_by_source(source_name)

        sources.append(
            SourceStatusSchema(
                name=source_name,
                enabled=True,
                last_run=last_run_obj.started_at.isoformat() if last_run_obj else None,
                last_status=last_run_obj.status if last_run_obj else None,
                total_runs=row.get("total_runs", 0),
                total_jobs_found=row.get("total_jobs_found", 0),
                total_jobs_new=row.get("total_jobs_new", 0),
            )
        )

    return SourcesResponse(sources=sources, total=len(sources))
