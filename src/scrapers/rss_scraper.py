"""
Generic RSS/Atom feed scraper.

Handles any standard RSS 2.0 or Atom 1.0 feed.
Parses: title, link, description, pubDate, author, tags.
"""
from typing import List, Optional
from urllib.parse import urlparse

import feedparser

from src.scrapers.base_scraper import BaseScraper, RawJobData
from src.monitoring.logger import get_logger

log = get_logger(__name__)

# Fields that commonly contain salary in job RSS feeds
_SALARY_FIELDS = ["salary", "compensation", "pay", "wage"]


class RssScraper(BaseScraper):
    """
    Scrapes job listings from any RSS/Atom feed URL.

    Can be used as a base for more specific scrapers or directly
    for any standard RSS job feed.
    """

    source_name: str = "rss"

    def __init__(self, feed_url: str, source_name: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._feed_url = feed_url
        if source_name:
            self.source_name = source_name

    async def _fetch_jobs(self) -> List[RawJobData]:
        """Fetch and parse an RSS/Atom feed."""
        log.info("rss_fetch_start", url=self._feed_url, source=self.source_name)

        response = await self._get(self._feed_url)
        feed = feedparser.parse(response.text)

        if feed.bozo and feed.bozo_exception:
            # feedparser uses "bozo" flag for malformed feeds
            log.warning(
                "rss_malformed_feed",
                url=self._feed_url,
                error=str(feed.bozo_exception)[:200],
            )

        if not feed.entries:
            log.warning("rss_no_entries", url=self._feed_url)
            return []

        jobs = []
        for entry in feed.entries:
            try:
                job = self._parse_entry(entry)
                if job:
                    jobs.append(job)
            except Exception as exc:
                log.warning(
                    "rss_entry_parse_error",
                    source=self.source_name,
                    entry_id=getattr(entry, "id", "unknown"),
                    error=str(exc),
                )

        log.info("rss_fetch_complete", source=self.source_name, job_count=len(jobs))
        return jobs

    def _parse_entry(self, entry) -> Optional[RawJobData]:
        """
        Parse a single feedparser entry into RawJobData.
        Returns None if essential fields are missing.
        """
        title = _get_text(entry, "title", "")
        url = _get_text(entry, "link", "")

        if not title or not url:
            return None

        # Try to extract company from title (common pattern: "Role at Company")
        company = ""
        if " at " in title.lower():
            parts = title.lower().split(" at ", 1)
            company = parts[-1].strip().title()
        if not company:
            # Fall back to feed title or domain
            company = getattr(entry, "author", None) or _domain_as_company(url)

        # Description / summary
        description = ""
        if hasattr(entry, "content") and entry.content:
            description = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            description = entry.summary or ""

        # Tags / categories
        tags: List[str] = []
        if hasattr(entry, "tags"):
            tags = [t.term for t in entry.tags if hasattr(t, "term")]

        # Published date
        posted_at_raw = None
        if hasattr(entry, "published"):
            posted_at_raw = entry.published
        elif hasattr(entry, "updated"):
            posted_at_raw = entry.updated

        # Salary (look in description or dedicated fields)
        salary_raw = None
        for field_name in _SALARY_FIELDS:
            val = getattr(entry, field_name, None)
            if val:
                salary_raw = str(val)
                break

        # External ID: use guid/id if available
        external_id = getattr(entry, "id", None) or url

        return RawJobData(
            source=self.source_name,
            external_id=external_id,
            title=title.strip(),
            company=company.strip(),
            location=None,  # Generic RSS rarely has structured location
            url=url.strip(),
            description=description[:5000] if description else None,
            salary_raw=salary_raw,
            tags=tags[:20],
            posted_at_raw=posted_at_raw,
            remote=_infer_remote(title, description),
        )


# ---- Helper functions ----

def _get_text(obj, attr: str, default: str = "") -> str:
    """Safely get a string attribute from a feedparser object."""
    val = getattr(obj, attr, default)
    return str(val).strip() if val else default


def _domain_as_company(url: str) -> str:
    """Extract a company name hint from a URL domain."""
    try:
        domain = urlparse(url).netloc
        # Remove www. and TLD
        parts = domain.replace("www.", "").split(".")
        return parts[0].title() if parts else "Unknown"
    except Exception:
        return "Unknown"


def _infer_remote(title: str, description: str) -> Optional[bool]:
    """Heuristic: check if title/description mentions remote work."""
    combined = f"{title} {description}".lower()
    if any(kw in combined for kw in ["remote", "work from home", "wfh", "distributed"]):
        return True
    if any(kw in combined for kw in ["on-site", "onsite", "in office", "in-office"]):
        return False
    return None
