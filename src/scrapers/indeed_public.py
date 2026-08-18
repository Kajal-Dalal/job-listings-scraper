"""
Indeed public RSS feed scraper.

URL pattern: https://www.indeed.com/rss?q={query}&l={location}
No authentication required. Publicly accessible RSS feed.

Note: Indeed's RSS feeds are publicly available and indexed by Google.
We scrape only public, non-auth-walled content and respect robots.txt
rate limiting principles (see ETHICS_AND_LIMITS.md).
"""
import re
from typing import List, Optional
from urllib.parse import quote_plus

import feedparser

from src.scrapers.base_scraper import BaseScraper, RawJobData
from src.monitoring.logger import get_logger

log = get_logger(__name__)

INDEED_RSS_BASE = "https://www.indeed.com/rss"


class IndeedRssScraper(BaseScraper):
    """
    Scrapes job listings from Indeed's public RSS feed.

    Supports configurable query and location.
    Handles pagination by fetching multiple pages.
    """

    source_name: str = "indeed_rss"

    def __init__(
        self,
        query: str = "software engineer",
        location: str = "remote",
        max_pages: int = 1,
        results_per_page: int = 25,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._query = query
        self._location = location
        self._max_pages = max_pages
        self._results_per_page = results_per_page

    def _build_url(self, start: int = 0) -> str:
        """Build the Indeed RSS URL for a given pagination offset."""
        params = {
            "q": self._query,
            "l": self._location,
            "sort": "date",
            "start": str(start),
            "limit": str(self._results_per_page),
        }
        param_str = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
        return f"{INDEED_RSS_BASE}?{param_str}"

    async def _fetch_jobs(self) -> List[RawJobData]:
        """Fetch one or more pages of Indeed RSS results."""
        all_jobs: List[RawJobData] = []

        for page in range(self._max_pages):
            start = page * self._results_per_page
            url = self._build_url(start=start)
            log.info(
                "indeed_rss_fetch_page",
                page=page + 1,
                start=start,
                url=url,
            )

            try:
                page_jobs = await self._fetch_page(url)
                all_jobs.extend(page_jobs)

                if len(page_jobs) < self._results_per_page:
                    # Fewer results than requested means we've hit the last page
                    break
            except Exception as exc:
                log.error(
                    "indeed_rss_page_error",
                    page=page + 1,
                    url=url,
                    error=str(exc),
                )
                if page == 0:
                    raise  # Re-raise on first page failure
                break  # Gracefully stop pagination on later-page errors

        log.info("indeed_rss_fetch_complete", total_jobs=len(all_jobs))
        return all_jobs

    async def _fetch_page(self, url: str) -> List[RawJobData]:
        """Fetch and parse a single RSS page."""
        response = await self._get(
            url,
            extra_headers={
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Referer": "https://www.indeed.com/",
            },
        )

        feed = feedparser.parse(response.text)

        if feed.bozo and not feed.entries:
            raise ValueError(
                f"Failed to parse Indeed RSS: {getattr(feed, 'bozo_exception', 'unknown')}"
            )

        jobs = []
        for entry in feed.entries:
            try:
                job = self._parse_entry(entry)
                if job:
                    jobs.append(job)
            except Exception as exc:
                log.warning(
                    "indeed_rss_entry_error",
                    entry_id=getattr(entry, "id", "unknown"),
                    error=str(exc),
                )

        return jobs

    def _parse_entry(self, entry) -> Optional[RawJobData]:
        """Parse a single Indeed RSS entry."""
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()

        if not title or not url:
            return None

        # Indeed RSS title format: "Job Title - Company Name - Location"
        company, location = self._parse_title(title, url)

        # Description / summary
        description = ""
        if hasattr(entry, "summary"):
            description = entry.summary or ""

        # Extract salary from description if present
        salary_raw = _extract_salary_from_description(description)

        # Remote detection
        remote = _infer_remote(title, description, self._location)

        # Published date
        posted_at_raw = None
        if hasattr(entry, "published"):
            posted_at_raw = entry.published
        elif hasattr(entry, "updated"):
            posted_at_raw = entry.updated

        return RawJobData(
            source=self.source_name,
            external_id=url,
            title=title.strip(),
            company=company,
            location=location,
            url=url,
            description=description[:5000] if description else None,
            salary_raw=salary_raw,
            tags=_extract_tags(title, description),
            posted_at_raw=posted_at_raw,
            remote=remote,
        )

    @staticmethod
    def _parse_title(title: str, url: str) -> tuple[str, Optional[str]]:
        """
        Parse Indeed RSS title into (company, location).

        Indeed title format: "Job Title - Company - City, State"
        or: "Job Title - Company"
        """
        parts = [p.strip() for p in title.split(" - ")]

        if len(parts) >= 3:
            # Assume: title - company - location
            company = parts[1]
            location = parts[2]
        elif len(parts) == 2:
            # Assume: title - company
            company = parts[1]
            location = None
        else:
            # Can't parse
            company = _extract_company_from_url(url)
            location = None

        return company or "Unknown", location


# ---- Helper functions ----

_SALARY_RE = re.compile(
    r"""
    [\$£€]?\s*[\d,]+\s*[kK]?\s*[-–—to]+\s*[\$£€]?\s*[\d,]+\s*[kK]?
    (?:\s*(?:per\s+year|annually|/yr|/year|pa))?
    """,
    re.VERBOSE,
)


def _extract_salary_from_description(description: str) -> Optional[str]:
    """Extract salary info from HTML description."""
    # Strip HTML tags first
    clean = re.sub(r"<[^>]+>", " ", description)
    m = _SALARY_RE.search(clean)
    return m.group(0).strip() if m else None


def _extract_tags(title: str, description: str) -> List[str]:
    """Extract technology keywords from job content."""
    combined = f"{title} {description}".lower()
    tech_keywords = [
        "python", "java", "javascript", "typescript", "react", "node",
        "django", "flask", "spring", "aws", "azure", "gcp", "docker",
        "kubernetes", "sql", "nosql", "mongodb", "postgresql", "redis",
        "machine learning", "data science", "devops", "backend", "frontend",
        "fullstack", "mobile", "ios", "android", "golang", "rust", "scala",
    ]
    return [kw for kw in tech_keywords if re.search(r"\b" + re.escape(kw) + r"\b", combined)][:10]


def _infer_remote(title: str, description: str, search_location: str) -> Optional[bool]:
    """Infer remote status from title/description/search location."""
    combined = f"{title} {description} {search_location}".lower()
    if any(kw in combined for kw in ["remote", "work from home", "wfh"]):
        return True
    if any(kw in combined for kw in ["on-site", "onsite", "in office"]):
        return False
    return None


def _extract_company_from_url(url: str) -> str:
    """Last-resort company extraction from URL."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        # Indeed URLs often have employer in query string
        if "employer" in url:
            m = re.search(r"employer=([^&]+)", url)
            if m:
                return m.group(1).replace("+", " ").title()
        return parsed.netloc.replace("www.", "").split(".")[0].title()
    except Exception:
        return "Unknown"
