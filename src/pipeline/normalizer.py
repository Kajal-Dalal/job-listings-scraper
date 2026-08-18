"""
Schema normalizer: converts RawJobData into JobListing ORM objects.

Handles:
- Salary string → (min, max, currency) integers
- Date string → UTC datetime
- Location normalization
- HTML stripping from descriptions
- Tag deduplication
"""
import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple
from uuid import uuid4

from src.scrapers.base_scraper import RawJobData
from src.storage.models import JobListing
from src.utils.validators import parse_salary, strip_html, truncate
from src.monitoring.logger import get_logger

log = get_logger(__name__)

# Mapping of source timezone abbreviations to UTC offsets (approximate)
_TZ_OFFSETS = {
    "PST": "-0800", "PDT": "-0700",
    "MST": "-0700", "MDT": "-0600",
    "CST": "-0600", "CDT": "-0500",
    "EST": "-0500", "EDT": "-0400",
    "GMT": "+0000", "UTC": "+0000",
    "BST": "+0100", "CET": "+0100",
    "IST": "+0530",
}


class Normalizer:
    """
    Converts RawJobData objects into JobListing ORM instances.
    Pure transformation logic — no I/O.
    """

    def normalize(self, raw: RawJobData) -> Optional[JobListing]:
        """
        Transform a RawJobData into a JobListing.

        Returns None if the data is too incomplete to be useful.
        """
        # Required fields
        title = _clean_text(raw.title)
        company = _clean_text(raw.company)
        url = (raw.url or "").strip()

        if not title or len(title) < 3:
            log.debug("normalizer_skip_no_title", source=raw.source)
            return None
        if not url:
            log.debug("normalizer_skip_no_url", source=raw.source, title=title[:50])
            return None

        company = company or "Unknown"

        # Salary
        salary_min, salary_max, salary_currency = self._normalise_salary(raw.salary_raw)

        # Dates
        posted_at = self._normalise_date(raw.posted_at_raw)

        # Description
        description = None
        if raw.description:
            description = truncate(strip_html(raw.description), max_length=5000)

        # Tags
        tags = self._normalise_tags(raw.tags)

        # Remote detection
        remote = raw.remote
        if remote is None:
            remote = self._infer_remote(title, description or "", raw.location or "")

        # Location
        location = self._normalise_location(raw.location, remote)

        # Dedup hash
        dedup_hash = _compute_hash(url=url, title=title, company=company)

        job = JobListing(
            id=str(uuid4()),
            source=raw.source,
            external_id=raw.external_id,
            title=title,
            company=company,
            location=location,
            remote=remote or False,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            description=description,
            url=url,
            tags=tags,
            hash=dedup_hash,
            scraped_at=datetime.utcnow(),
            posted_at=posted_at,
        )
        return job

    def normalize_many(self, raw_jobs: List[RawJobData]) -> List[JobListing]:
        """Normalize a list of raw jobs. Skips invalid entries."""
        results = []
        for raw in raw_jobs:
            try:
                job = self.normalize(raw)
                if job:
                    results.append(job)
            except Exception as exc:
                log.warning(
                    "normalizer_error",
                    source=raw.source,
                    title=str(raw.title)[:50],
                    error=str(exc),
                )
        return results

    @staticmethod
    def _normalise_salary(
        raw: Optional[str],
    ) -> Tuple[Optional[int], Optional[int], str]:
        """Parse salary string into (min, max, currency)."""
        if not raw:
            return None, None, "USD"
        try:
            return parse_salary(raw)
        except Exception:
            return None, None, "USD"

    @staticmethod
    def _normalise_date(raw: Optional[str]) -> Optional[datetime]:
        """Parse a date string to UTC datetime."""
        if not raw:
            return None

        # Try unix timestamp
        try:
            ts = float(raw)
            return datetime.utcfromtimestamp(ts)
        except (ValueError, TypeError):
            pass

        # Replace timezone abbreviations with UTC offsets
        for abbr, offset in _TZ_OFFSETS.items():
            raw = raw.replace(abbr, offset)

        # Try RFC 2822 (used by RSS)
        try:
            return parsedate_to_datetime(raw).replace(tzinfo=None)
        except Exception:
            pass

        # Try ISO 8601
        formats = [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d %b %Y",
            "%B %d, %Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(raw.strip(), fmt)
            except (ValueError, TypeError):
                continue

        log.debug("normalizer_date_parse_failed", raw=raw[:50])
        return None

    @staticmethod
    def _normalise_tags(tags: List[str]) -> List[str]:
        """Deduplicate and clean tags."""
        seen = set()
        result = []
        for tag in tags:
            clean = tag.strip().lower()[:50]
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result[:20]

    @staticmethod
    def _infer_remote(title: str, description: str, location: str) -> bool:
        """Heuristic remote detection from combined text."""
        combined = f"{title} {description} {location}".lower()
        remote_kws = ["remote", "work from home", "wfh", "distributed", "anywhere"]
        return any(kw in combined for kw in remote_kws)

    @staticmethod
    def _normalise_location(location: Optional[str], remote: Optional[bool]) -> Optional[str]:
        """Clean and normalise location string."""
        if not location or location.lower() in ("n/a", "null", "none", "unknown"):
            return "Remote" if remote else None
        # Clean up extra whitespace
        cleaned = re.sub(r"\s+", " ", location.strip())
        # Cap length
        return cleaned[:256] if cleaned else None


# ---- Module-level helpers ----

def _clean_text(text: Optional[str]) -> str:
    """Strip HTML and normalise whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", strip_html(text)).strip()


def _compute_hash(url: str, title: str, company: str) -> str:
    """
    Compute a SHA-256 dedup hash from (url, title, company).
    Case-insensitive so minor capitalisation changes don't create duplicates.
    """
    key = f"{url.lower().strip()}|{title.lower().strip()}|{company.lower().strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
