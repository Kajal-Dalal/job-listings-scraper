"""
Hacker News "Who's Hiring" scraper.

Uses the HN Algolia API (completely public, no auth):
  https://hn.algolia.com/api/v1/search?tags=ask_hn,jobs

Parses freeform job post text using regex patterns to extract
structured data (company, location, salary, remote, technologies).
"""
import re
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import urlencode

from src.scrapers.base_scraper import BaseScraper, RawJobData
from src.monitoring.logger import get_logger

log = get_logger(__name__)

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1/search"

# Regex patterns for extracting structured info from freeform HN posts
_REMOTE_PATTERNS = [
    re.compile(r"\bremote\b", re.IGNORECASE),
    re.compile(r"\bwfh\b", re.IGNORECASE),
    re.compile(r"\bwork\s+from\s+home\b", re.IGNORECASE),
    re.compile(r"\bdistributed\s+team\b", re.IGNORECASE),
]

_ONSITE_PATTERNS = [
    re.compile(r"\bon[\s-]?site\b", re.IGNORECASE),
    re.compile(r"\bin[\s-]?office\b", re.IGNORECASE),
    re.compile(r"\bno\s+remote\b", re.IGNORECASE),
]

_SALARY_PATTERN = re.compile(
    r"""
    (?:salary|comp|compensation|pay|wage)?\s*:?\s*
    (?P<currency>[\$£€])?\s*
    (?P<min>[\d,]+)\s*[kK]?
    \s*[-–—to]+\s*
    (?P<currency2>[\$£€])?\s*
    (?P<max>[\d,]+)\s*[kK]?
    """,
    re.VERBOSE | re.IGNORECASE,
)

_LOCATION_PATTERN = re.compile(
    r"""
    (?:location|based\s+in|headquartered\s+in|office\s+in)\s*:?\s*
    ([A-Z][a-zA-Z\s,]{2,50})
    """,
    re.VERBOSE,
)

_TECH_KEYWORDS = {
    "python", "javascript", "typescript", "go", "golang", "rust", "java", "kotlin",
    "swift", "c++", "c#", "ruby", "php", "scala", "haskell", "erlang", "elixir",
    "react", "vue", "angular", "node.js", "django", "flask", "fastapi", "rails",
    "kubernetes", "docker", "aws", "gcp", "azure", "postgres", "mysql", "redis",
    "mongodb", "elasticsearch", "kafka", "spark", "tensorflow", "pytorch", "llm",
    "machine learning", "ml", "ai", "devops", "sre", "backend", "frontend",
    "fullstack", "full-stack", "mobile", "ios", "android",
}


class HNJobsScraper(BaseScraper):
    """
    Scrapes job listings from HN 'Who's Hiring' threads via Algolia API.

    The API is fully public and does not require authentication.
    """

    source_name: str = "hn_jobs"

    def __init__(self, max_results: int = 50, **kwargs):
        super().__init__(**kwargs)
        self._max_results = max_results

    async def _fetch_jobs(self) -> List[RawJobData]:
        """Fetch HN job posts from Algolia search API."""
        params = {
            "tags": "ask_hn,jobs",
            "hitsPerPage": min(self._max_results, 100),
            "attributesToRetrieve": "title,url,author,created_at,points,objectID,_tags",
        }
        url = f"{HN_ALGOLIA_BASE}?{urlencode(params)}"
        log.info("hn_jobs_fetch_start", url=url)

        response = await self._get(url, extra_headers={"Accept": "application/json"})

        try:
            data = response.json()
        except Exception as exc:
            raise ValueError(f"Failed to parse HN Algolia JSON: {exc}") from exc

        hits = data.get("hits", [])
        if not hits:
            log.warning("hn_jobs_no_hits")
            return []

        jobs = []
        for hit in hits:
            try:
                job = self._parse_hit(hit)
                if job:
                    jobs.append(job)
            except Exception as exc:
                log.warning(
                    "hn_jobs_parse_error",
                    object_id=hit.get("objectID"),
                    error=str(exc),
                )

        log.info("hn_jobs_fetch_complete", job_count=len(jobs))
        return jobs

    def _parse_hit(self, hit: dict) -> Optional[RawJobData]:
        """Parse a single Algolia hit into RawJobData."""
        title = (hit.get("title") or "").strip()
        hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        external_url = hit.get("url") or hn_url
        author = hit.get("author") or "Unknown"
        created_at = hit.get("created_at") or ""
        text = hit.get("title", "") or ""

        if not title:
            return None

        # Extract structured fields from title text
        company = self._extract_company(title, author)
        location = self._extract_location(text)
        salary_raw = self._extract_salary(text)
        remote = self._extract_remote(text)
        tags = self._extract_tags(text)

        return RawJobData(
            source=self.source_name,
            external_id=str(hit.get("objectID", hn_url)),
            title=title,
            company=company,
            location=location,
            url=hn_url,  # Always link to HN thread
            description=text[:5000] if text else None,
            salary_raw=salary_raw,
            tags=tags,
            posted_at_raw=created_at,
            remote=remote,
            extra={"points": hit.get("points", 0), "author": author, "external_url": external_url},
        )

    @staticmethod
    def _extract_company(title: str, author: str) -> str:
        """
        Try to extract company name from HN job title.

        Common HN formats:
          - "Company Name | Role | Location"
          - "Company Name - Role"
          - "Company Name (hiring)"
        """
        # Pattern: "Company | ..."
        if " | " in title:
            return title.split(" | ")[0].strip()
        # Pattern: "Company - ..."
        if " - " in title and not title.startswith(" - "):
            return title.split(" - ")[0].strip()
        # Pattern: "Company (hiring ...)"
        paren_match = re.match(r"^([A-Z][^(]+?)\s*\(", title)
        if paren_match:
            return paren_match.group(1).strip()
        # Fallback: use HN username (often matches company)
        return author.replace("_", " ").title() if author else "Unknown"

    @staticmethod
    def _extract_location(text: str) -> Optional[str]:
        """Extract location from free-form HN job text."""
        m = _LOCATION_PATTERN.search(text)
        if m:
            return m.group(1).strip().rstrip(",.")
        # Look for city/country patterns like "San Francisco, CA" in text
        city_pattern = re.search(
            r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)*(?:,\s*[A-Z]{2})?)\b", text
        )
        if city_pattern and len(city_pattern.group(1)) > 3:
            return city_pattern.group(1).strip()
        return None

    @staticmethod
    def _extract_salary(text: str) -> Optional[str]:
        """Extract salary range from free-form text."""
        m = _SALARY_PATTERN.search(text)
        if m:
            currency = m.group("currency") or m.group("currency2") or "$"
            min_val = m.group("min").replace(",", "")
            max_val = m.group("max").replace(",", "")
            # Handle K suffix in original text
            if "k" in text[max(0, m.start() - 2): m.end() + 2].lower():
                try:
                    min_int = int(min_val) * 1000 if int(min_val) < 1000 else int(min_val)
                    max_int = int(max_val) * 1000 if int(max_val) < 1000 else int(max_val)
                    return f"{currency}{min_int:,} - {currency}{max_int:,}"
                except ValueError:
                    pass
            return f"{currency}{min_val} - {currency}{max_val}"
        return None

    @staticmethod
    def _extract_remote(text: str) -> Optional[bool]:
        """Determine if the job is remote based on text keywords."""
        for pattern in _REMOTE_PATTERNS:
            if pattern.search(text):
                return True
        for pattern in _ONSITE_PATTERNS:
            if pattern.search(text):
                return False
        return None

    @staticmethod
    def _extract_tags(text: str) -> List[str]:
        """Extract technology tags from free-form job text."""
        text_lower = text.lower()
        found = []
        for keyword in _TECH_KEYWORDS:
            # Use word boundary matching for short keywords
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, text_lower):
                found.append(keyword)
        return found[:15]
