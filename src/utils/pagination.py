"""
Cursor-based pagination — production-grade alternative to offset pagination.

Why cursor > offset (from FreshMart's product listing learnings):
- Offset pagination breaks on live data: if a new job is inserted between page 1 and page 2,
  you either see it twice or miss it entirely.
- Cursor pagination is stable: it uses the last-seen scraped_at+id as an anchor.

Both modes are supported:
  - Offset: simple page/page_size (existing API, backward-compatible)
  - Cursor: cursor token (opaque, base64-encoded) for stable infinite scroll

Usage:
    # Encode cursor from last item in a page
    cursor = encode_cursor(last_job.scraped_at, last_job.id)

    # Decode cursor for next query
    scraped_at, job_id = decode_cursor(cursor)
"""
import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, List, Optional, TypeVar

T = TypeVar("T")


def encode_cursor(scraped_at: datetime, job_id: str) -> str:
    """
    Encode a cursor from (scraped_at, id) pair.
    Returns an opaque base64 string safe to use in URLs.
    """
    payload = json.dumps({
        "ts": scraped_at.isoformat(),
        "id": job_id,
    })
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """
    Decode a cursor token back to (scraped_at, id).

    Raises ValueError if the cursor is malformed.
    """
    try:
        payload = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(payload)
        return datetime.fromisoformat(data["ts"]), data["id"]
    except Exception as exc:
        raise ValueError(f"Invalid pagination cursor: {exc}") from exc


@dataclass
class CursorPage(Generic[T]):
    """
    A single page of results with cursor-based navigation metadata.

    Compatible with the existing PaginatedJobsResponse but adds cursor support.
    """
    items: List[T]
    total: int
    has_next: bool
    has_prev: bool
    next_cursor: Optional[str]      # Pass this as ?cursor= for the next page
    prev_cursor: Optional[str]      # Pass this as ?cursor= for the previous page
    page_size: int

    # Also keep offset-pagination fields for backward compatibility
    page: int = 1
    total_pages: int = 1


@dataclass
class OffsetPage(Generic[T]):
    """Standard offset-based page (existing behaviour)."""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool

    @classmethod
    def from_query(
        cls,
        items: List[T],
        total: int,
        page: int,
        page_size: int,
    ) -> "OffsetPage[T]":
        total_pages = max(1, (total + page_size - 1) // page_size)
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        )
