"""
RemoteOK public API scraper.

Endpoint: https://remoteok.com/api
Returns JSON array of job objects.
No authentication required. Public API.
"""
import json
from typing import List, Optional

from src.scrapers.base_scraper import BaseScraper, RawJobData
from src.monitoring.logger import get_logger

log = get_logger(__name__)

REMOTEOK_API_URL = "https://remoteok.com/api"


class RemoteOKScraper(BaseScraper):
    """
    Scrapes job listings from the RemoteOK public API.

    API returns a JSON array where:
    - First element is a legal/info object (skipped)
    - Subsequent elements are job objects
    """

    source_name: str = "remoteok"

    def __init__(self, max_results: int = 100, **kwargs):
        super().__init__(**kwargs)
        self._max_results = max_results

    async def _fetch_jobs(self) -> List[RawJobData]:
        log.info("remoteok_fetch_start", url=REMOTEOK_API_URL)

        # RemoteOK requires a realistic User-Agent; send Accept: application/json
        response = await self._get(
            REMOTEOK_API_URL,
            extra_headers={
                "Accept": "application/json",
                "Referer": "https://remoteok.com/",
            },
        )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            log.error("remoteok_json_parse_error", error=str(exc))
            raise ValueError(f"Failed to parse RemoteOK JSON: {exc}") from exc

        if not isinstance(data, list) or len(data) < 2:
            log.warning("remoteok_unexpected_format", length=len(data) if data else 0)
            return []

        # Skip the first element (legal notice)
        job_entries = data[1:self._max_results + 1]
        jobs = []

        for entry in job_entries:
            try:
                job = self._parse_job(entry)
                if job:
                    jobs.append(job)
            except Exception as exc:
                log.warning(
                    "remoteok_entry_parse_error",
                    entry_id=entry.get("id", "unknown"),
                    error=str(exc),
                )

        log.info("remoteok_fetch_complete", job_count=len(jobs))
        return jobs

    def _parse_job(self, data: dict) -> Optional[RawJobData]:
        """Parse a single RemoteOK job object."""
        # Validate required fields
        title = (data.get("position") or data.get("title") or "").strip()
        company = (data.get("company") or "").strip()
        url = (data.get("url") or "").strip()

        if not title or not url:
            return None

        # Ensure URL is absolute
        if url.startswith("/"):
            url = f"https://remoteok.com{url}"

        # Tags
        tags: List[str] = []
        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if t][:20]

        # Salary
        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        salary_raw = None
        if salary_min or salary_max:
            parts = []
            if salary_min:
                parts.append(f"${salary_min:,}")
            if salary_max:
                parts.append(f"${salary_max:,}")
            salary_raw = " - ".join(parts) if len(parts) > 1 else parts[0]

        # Description (HTML strip will happen in normalizer)
        description = data.get("description", "") or ""

        # Location (RemoteOK jobs are remote by nature)
        location = data.get("location") or "Remote"

        # Posted at
        posted_at_raw = data.get("date") or data.get("epoch")
        if isinstance(posted_at_raw, (int, float)):
            # Unix timestamp
            posted_at_raw = str(int(posted_at_raw))

        return RawJobData(
            source=self.source_name,
            external_id=str(data.get("id") or data.get("slug") or url),
            title=title,
            company=company or "Unknown",
            location=location,
            url=url,
            description=description[:5000],
            salary_raw=salary_raw,
            tags=tags,
            posted_at_raw=str(posted_at_raw) if posted_at_raw else None,
            remote=True,  # RemoteOK is explicitly remote jobs
            extra={
                "slug": data.get("slug"),
                "company_url": data.get("company_url"),
                "logo": data.get("logo"),
            },
        )
