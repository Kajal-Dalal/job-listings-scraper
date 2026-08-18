"""
Health check endpoints — FreshMart-style readiness and liveness probes.

GET /health          — full system health (Kubernetes readiness probe)
GET /health/live     — minimal liveness check (just: is the process alive?)
GET /health/ready    — readiness check (DB + scheduler ready to serve traffic)
"""
import time
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import HealthResponse
from src.monitoring.logger import get_logger
from src.storage.database import get_db_session
from src.utils.circuit_breaker import get_all_breaker_statuses

log = get_logger(__name__)
router = APIRouter()

# Track app start time for uptime calculation
_START_TIME = time.time()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Full System Health",
    description=(
        "Comprehensive health check — DB, scheduler, circuit breakers, uptime. "
        "Use as Kubernetes **readiness** probe."
    ),
    tags=["health"],
)
async def health_check(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HealthResponse:
    """Full health check including DB, scheduler, and circuit breaker states."""
    # --- Database health ---
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {str(exc)[:100]}"
        log.error("health_check_db_error", error=str(exc))

    # --- Scheduler status ---
    scheduler_status: Dict[str, Any] = {"enabled": False}
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        scheduler_status = scheduler.get_status()

    # --- Last scrape ---
    last_scrape = None
    try:
        from src.storage.repository import ScrapeRunRepository
        run_repo = ScrapeRunRepository(session)
        recent = await run_repo.get_recent(limit=1)
        if recent:
            last_scrape = recent[0].started_at.isoformat()
    except Exception:
        pass

    # --- Circuit breakers (FreshMart-inspired: expose all service states) ---
    circuit_breakers = get_all_breaker_statuses()

    # --- Overall status ---
    overall = "ok"
    if db_status != "ok":
        overall = "degraded"

    # Degraded if any circuit is OPEN
    open_circuits = [cb for cb in circuit_breakers if cb.get("state") == "open"]
    if open_circuits and overall == "ok":
        overall = "degraded"

    uptime = time.time() - _START_TIME

    return HealthResponse(
        status=overall,
        db=db_status,
        scheduler=scheduler_status,
        last_scrape=last_scrape,
        uptime_seconds=round(uptime, 2),
        circuit_breakers=circuit_breakers,
    )


@router.get(
    "/health/live",
    summary="Liveness Probe",
    description="Minimal check — just confirms the process is alive. Use as Kubernetes **liveness** probe.",
    tags=["health"],
    status_code=200,
)
async def liveness() -> dict:
    """Kubernetes liveness probe — always returns 200 if process is running."""
    return {"status": "alive", "timestamp": datetime.utcnow().isoformat()}


@router.get(
    "/health/ready",
    summary="Readiness Probe",
    description=(
        "Confirms the app is ready to serve traffic: DB reachable and tables exist. "
        "Returns 503 if not ready."
    ),
    tags=["health"],
)
async def readiness(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Kubernetes readiness probe — returns 503 if DB is not reachable."""
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "db": f"error: {str(exc)[:100]}"}
