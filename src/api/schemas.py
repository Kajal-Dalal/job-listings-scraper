"""
Pydantic v2 response schemas for the REST API.

Separate from ORM models to allow independent evolution of API and DB schemas.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class JobListingSchema(BaseModel):
    """Full job listing representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    external_id: Optional[str] = None
    title: str
    company: str
    location: Optional[str] = None
    remote: bool
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = "USD"
    description: Optional[str] = None
    url: str
    tags: Optional[List[str]] = None
    scraped_at: datetime
    posted_at: Optional[datetime] = None
    created_at: datetime

    @field_serializer("scraped_at", "posted_at", "created_at")
    def serialise_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


class JobListingSummarySchema(BaseModel):
    """Lightweight job listing for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    title: str
    company: str
    location: Optional[str] = None
    remote: bool
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = "USD"
    url: str
    tags: Optional[List[str]] = None
    posted_at: Optional[datetime] = None
    scraped_at: datetime

    @field_serializer("scraped_at", "posted_at")
    def serialise_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


class PaginatedJobsResponse(BaseModel):
    """Paginated list of job listings."""

    items: List[JobListingSummarySchema]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool


class ScrapeRunSchema(BaseModel):
    """Representation of a scrape run."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    jobs_found: int
    jobs_new: int
    jobs_duplicate: int
    jobs_error: int
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None

    @field_serializer("started_at", "finished_at")
    def serialise_dt(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None


class HealthResponse(BaseModel):
    """Health check response — FreshMart-style with circuit breaker states."""

    status: str  # "ok" | "degraded" | "error"
    db: str       # "ok" | "error"
    scheduler: Dict[str, Any]
    last_scrape: Optional[str] = None
    version: str = "1.0.0"
    uptime_seconds: Optional[float] = None
    circuit_breakers: List[Dict[str, Any]] = []  # Per-source circuit state


class ScrapeTriggerResponse(BaseModel):
    """Response after manually triggering a scrape."""

    message: str
    triggered_at: str
    sources: List[str]


class SourceStatusSchema(BaseModel):
    """Status of a single scraper source."""

    name: str
    enabled: bool
    last_run: Optional[str] = None
    last_status: Optional[str] = None
    total_runs: int = 0
    total_jobs_found: int = 0
    total_jobs_new: int = 0


class SourcesResponse(BaseModel):
    """Response listing all configured sources."""

    sources: List[SourceStatusSchema]
    total: int


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None


class JobFilters(BaseModel):
    """Query parameters for job listing filters."""

    source: Optional[str] = Field(None, description="Filter by source name")
    location: Optional[str] = Field(None, description="Filter by location (partial match)")
    keyword: Optional[str] = Field(None, description="Search in title, company, description")
    remote_only: Optional[bool] = Field(None, description="Filter remote-only jobs")
    salary_min: Optional[int] = Field(None, ge=0, description="Minimum salary filter")
    page: int = Field(1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(20, ge=1, le=100, description="Results per page")
    order_by: str = Field(
        "scraped_at_desc",
        description="Sort order: scraped_at_desc|scraped_at_asc|salary_desc|title_asc",
    )


class CursorPaginatedJobsResponse(BaseModel):
    """
    Cursor-based paginated response — stable pagination for live data.
    Inspired by FreshMart's ProductService infinite-scroll product listing.
    """
    items: List[JobListingSummarySchema]
    total: int
    has_next: bool
    has_prev: bool
    next_cursor: Optional[str] = None   # Pass as ?cursor= for next page
    prev_cursor: Optional[str] = None   # Pass as ?cursor= for previous page
    page_size: int


class StatsResponse(BaseModel):
    """Platform-wide statistics — inspired by FreshMart's admin dashboard stats."""

    total_jobs: int
    jobs_by_source: Dict[str, int]
    remote_jobs: int
    total_scrape_runs: int
    last_scrape_at: Optional[str] = None
    sources_status: List[Dict[str, Any]] = []
