"""
Scrapers package.

All scrapers inherit BaseScraper and implement _fetch_jobs().
Built-in: rate limiting, UA rotation, circuit breaker, retry, metrics, events.
"""
from src.scrapers.base_scraper import BaseScraper, RawJobData, ScraperResult
from src.scrapers.hn_jobs_scraper import HNJobsScraper
from src.scrapers.indeed_public import IndeedRssScraper
from src.scrapers.remoteok_scraper import RemoteOKScraper
from src.scrapers.rss_scraper import RssScraper

__all__ = [
    "BaseScraper",
    "RawJobData",
    "ScraperResult",
    "RemoteOKScraper",
    "HNJobsScraper",
    "IndeedRssScraper",
    "RssScraper",
]
