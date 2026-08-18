"""
Job Listings Scraper — FastAPI application entrypoint.

Startup sequence:
1. Configure structured logging
2. Initialise database (create tables if needed)
3. Start the scraper scheduler
4. Mount API routes

Run with:
    uvicorn src.main:app --host 0.0.0.0 --port 8000
"""
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from src.api.middleware import setup_middleware
from src.api.routes import health, jobs, scraper
from src.config.settings import get_settings
from src.monitoring.logger import configure_logging, get_logger
from src.monitoring.metrics import metrics
from src.pipeline.ingestion import IngestionPipeline, build_scrapers
from src.pipeline.scheduler import Scheduler
from src.storage.database import init_database

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan: startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.
    All startup logic before `yield`, all cleanup after.
    """
    settings = get_settings()

    # 1. Configure logging first
    configure_logging(
        log_level=settings.log_level,
        log_format=settings.log_format,
    )
    log.info("app_starting", version="1.0.0", env=settings.log_format)

    # 2. Initialise database
    db = init_database(settings.database_url)
    await db.create_tables()
    app.state.db = db
    log.info("database_ready")

    # 3. Build pipeline
    scrapers = build_scrapers(settings)
    pipeline = IngestionPipeline(
        database=db,
        scrapers=scrapers,
        max_concurrent=settings.max_concurrent_scrapers,
    )
    app.state.pipeline = pipeline

    # 4. Start scheduler
    if settings.enable_scheduler:
        scheduler = Scheduler(interval_minutes=settings.scrape_interval_minutes)
        scheduler.start(pipeline.run)
        app.state.scheduler = scheduler
        log.info(
            "scheduler_ready",
            interval_minutes=settings.scrape_interval_minutes,
        )
    else:
        app.state.scheduler = None
        log.info("scheduler_disabled")

    log.info("app_ready", host=settings.api_host, port=settings.api_port)

    yield  # ← application runs here

    # ---- Shutdown ----
    log.info("app_shutting_down")

    if settings.enable_scheduler and app.state.scheduler:
        app.state.scheduler.stop()

    await db.close()
    log.info("app_shutdown_complete")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Job Listings Scraper",
        description=(
            "Production-grade async job scraper with anti-detection, "
            "scheduling, deduplication, circuit breakers, and a REST API.\n\n"
            "## Authentication\n"
            "Write endpoints require `X-API-Key` header.\n\n"
            "## Distributed Tracing\n"
            "Every request gets a `X-Correlation-ID` response header. "
            "Pass it in your requests to trace end-to-end (FreshMart pattern).\n\n"
            "## Rate Limiting\n"
            "100 requests per minute per IP on all endpoints.\n\n"
            "## Pagination\n"
            "- **Offset**: `GET /api/v1/jobs?page=2&page_size=20` — simple, for browsing\n"
            "- **Cursor**: `GET /api/v1/jobs/cursor?cursor=<token>` — stable, for live data"
        ),
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "jobs", "description": "Job listing endpoints — browse, filter, paginate"},
            {"name": "scraper", "description": "Scraper management — trigger, status, sources"},
            {"name": "health", "description": "Health, readiness, and liveness probes"},
        ],
    )

    # OpenAPI security scheme — API key in header
    from fastapi.openapi.utils import get_openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        schema["components"]["securitySchemes"] = {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "API key required for write operations (scrape trigger)",
            }
        }
        # Apply security to all paths that need it
        for path_data in schema.get("paths", {}).values():
            for op in path_data.values():
                if isinstance(op, dict) and "scrape/trigger" in str(op.get("operationId", "")):
                    op["security"] = [{"ApiKeyAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi

    # Middleware
    setup_middleware(app)

    # Routes
    app.include_router(health.router)
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(scraper.router, prefix="/api/v1")

    # Prometheus metrics endpoint
    if settings.enable_metrics:
        @app.get("/metrics", include_in_schema=False)
        async def prometheus_metrics() -> Response:
            output, content_type = metrics.get_metrics_output()
            return Response(content=output, media_type=content_type)

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", "unknown")
        log.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            correlation_id=correlation_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "correlation_id": correlation_id,
            },
        )

    return app


# Module-level app instance (used by uvicorn)
app = create_app()


def run() -> None:
    """Entry point for `job-scraper` console script."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_config=None,  # We handle logging ourselves
    )


if __name__ == "__main__":
    run()
