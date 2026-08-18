"""
SQLAlchemy 2.0 ORM models.

Models:
  - JobListing  : a single scraped job
  - ScrapeRun   : metadata about a scrape run (used for monitoring)
"""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class JobListing(Base):
    """
    Represents a single job listing scraped from any source.
    """

    __tablename__ = "job_listings"

    # Primary key
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )

    # Source metadata
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # Job content
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str] = mapped_column(String(256), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)

    # Salary
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(8), default="USD")

    # Content
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Structured tags: stored as JSON array
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)

    # Deduplication hash: SHA-256 of (url + title + company)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # Timestamps
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, nullable=False
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
        nullable=False,
    )

    # Indexes for common query patterns
    __table_args__ = (
        Index("ix_job_listings_source_scraped", "source", "scraped_at"),
        Index("ix_job_listings_company", "company"),
        Index("ix_job_listings_remote", "remote"),
        Index("ix_job_listings_salary_min", "salary_min"),
    )

    def __repr__(self) -> str:
        return f"<JobListing id={self.id} title={self.title!r} company={self.company!r}>"


class ScrapeRun(Base):
    """
    Metadata and statistics for a single scrape run.
    """

    __tablename__ = "scrape_runs"

    # Valid status values
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_PARTIAL = "partial"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid
    )

    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_RUNNING, nullable=False
    )

    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    jobs_error: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Duration in seconds (computed on finish)
    duration_seconds: Mapped[Optional[float]] = mapped_column(
        Integer, nullable=True
    )

    __table_args__ = (
        Index("ix_scrape_runs_source_started", "source", "started_at"),
        Index("ix_scrape_runs_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<ScrapeRun id={self.id} source={self.source!r} status={self.status!r}>"
        )
