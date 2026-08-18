"""
Tests for the RSS scraper and rate limiter.

Uses pytest-httpx to mock HTTP responses without making real network calls.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import REMOTEOK_SAMPLE, RSS_SAMPLE


# ---------------------------------------------------------------------------
# RssScraper tests
# ---------------------------------------------------------------------------

class TestRssScraper:
    """Tests for the generic RSS scraper."""

    @pytest.mark.asyncio
    async def test_parse_basic_rss_feed(self):
        """Scraper should parse a well-formed RSS feed and return RawJobData list."""
        from src.scrapers.rss_scraper import RssScraper

        scraper = RssScraper(feed_url="https://example.com/rss", source_name="test_rss")

        # Mock the HTTP GET
        mock_response = MagicMock()
        mock_response.text = RSS_SAMPLE
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response) as mock_get:
            jobs = await scraper._fetch_jobs()

        mock_get.assert_called_once_with("https://example.com/rss")
        assert len(jobs) == 2

        job = jobs[0]
        assert "Python Engineer" in job.title or "Python" in job.title
        assert job.url == "https://example.com/jobs/python-engineer"
        assert job.source == "test_rss"

    @pytest.mark.asyncio
    async def test_skips_entry_without_url(self):
        """Entries without a URL should be silently skipped."""
        from src.scrapers.rss_scraper import RssScraper

        no_url_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>No URL Job</title></item>
          <item><title>Has URL</title><link>https://example.com/job/1</link></item>
        </channel></rss>"""

        scraper = RssScraper(feed_url="https://example.com/rss")
        mock_response = MagicMock()
        mock_response.text = no_url_rss
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        # Only the entry with a URL should be returned
        assert len(jobs) == 1
        assert jobs[0].url == "https://example.com/job/1"

    @pytest.mark.asyncio
    async def test_handles_empty_feed(self):
        """Empty RSS feed should return empty list, not raise."""
        from src.scrapers.rss_scraper import RssScraper

        empty_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Empty Feed</title></channel></rss>"""

        scraper = RssScraper(feed_url="https://example.com/rss")
        mock_response = MagicMock()
        mock_response.text = empty_rss
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert jobs == []

    @pytest.mark.asyncio
    async def test_infers_remote_from_title(self):
        """Jobs with 'remote' in their title should be marked remote=True."""
        from src.scrapers.rss_scraper import RssScraper

        remote_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item>
            <title>Remote Python Developer</title>
            <link>https://example.com/jobs/remote-py</link>
            <description>Fully remote position worldwide.</description>
          </item>
        </channel></rss>"""

        scraper = RssScraper(feed_url="https://example.com/rss")
        mock_response = MagicMock()
        mock_response.text = remote_rss
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert len(jobs) == 1
        assert jobs[0].remote is True


# ---------------------------------------------------------------------------
# RemoteOKScraper tests
# ---------------------------------------------------------------------------

class TestRemoteOKScraper:
    """Tests for the RemoteOK API scraper."""

    @pytest.mark.asyncio
    async def test_parses_remoteok_json(self):
        """Should parse RemoteOK JSON response and return job list."""
        from src.scrapers.remoteok_scraper import RemoteOKScraper

        scraper = RemoteOKScraper(max_results=10)
        mock_response = MagicMock()
        mock_response.json.return_value = REMOTEOK_SAMPLE
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert len(jobs) == 2
        assert jobs[0].title == "Senior Python Developer"
        assert jobs[0].company == "RemoteFirst Inc"
        assert jobs[0].remote is True
        assert jobs[0].source == "remoteok"
        assert "python" in jobs[0].tags

    @pytest.mark.asyncio
    async def test_skips_legal_header(self):
        """First element (legal notice) should always be skipped."""
        from src.scrapers.remoteok_scraper import RemoteOKScraper

        only_legal = [{"legal": "disclaimer text only"}]
        scraper = RemoteOKScraper()
        mock_response = MagicMock()
        mock_response.json.return_value = only_legal
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert jobs == []

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self):
        """Invalid JSON response should raise ValueError."""
        from src.scrapers.remoteok_scraper import RemoteOKScraper

        scraper = RemoteOKScraper()
        mock_response = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("Invalid", "", 0)

        with patch.object(scraper, "_get", return_value=mock_response):
            with pytest.raises(ValueError, match="Failed to parse RemoteOK JSON"):
                await scraper._fetch_jobs()

    @pytest.mark.asyncio
    async def test_max_results_limit(self):
        """max_results should cap number of jobs returned."""
        from src.scrapers.remoteok_scraper import RemoteOKScraper

        # Build a large list
        large_sample = [{"legal": "ok"}] + [
            {
                "id": i,
                "position": f"Job {i}",
                "company": "Corp",
                "url": f"/jobs/{i}",
            }
            for i in range(20)
        ]

        scraper = RemoteOKScraper(max_results=5)
        mock_response = MagicMock()
        mock_response.json.return_value = large_sample
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert len(jobs) <= 5


# ---------------------------------------------------------------------------
# HNJobsScraper tests
# ---------------------------------------------------------------------------

class TestHNJobsScraper:
    """Tests for the HN Who's Hiring scraper."""

    @pytest.mark.asyncio
    async def test_parses_algolia_response(self):
        """Should parse HN Algolia API response correctly."""
        from src.scrapers.hn_jobs_scraper import HNJobsScraper

        algolia_response = {
            "hits": [
                {
                    "objectID": "12345678",
                    "title": "TechCorp | Backend Engineer | Remote | $120k-$160k",
                    "url": "https://techcorp.com/jobs",
                    "author": "techcorp_hiring",
                    "created_at": "2024-01-15T10:00:00.000Z",
                    "points": 5,
                }
            ],
            "nbHits": 1,
        }

        scraper = HNJobsScraper(max_results=10)
        mock_response = MagicMock()
        mock_response.json.return_value = algolia_response
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source == "hn_jobs"
        assert "TechCorp" in job.company
        assert "ycombinator.com" in job.url  # Always links to HN thread

    @pytest.mark.asyncio
    async def test_extracts_company_from_pipe_format(self):
        """Company should be extracted from 'Company | Role | Location' format."""
        from src.scrapers.hn_jobs_scraper import HNJobsScraper

        company = HNJobsScraper._extract_company("MyStartup | Senior Engineer | SF", "user")
        assert company == "MyStartup"

    @pytest.mark.asyncio
    async def test_extracts_remote_flag(self):
        """Remote keyword in title should set remote=True."""
        from src.scrapers.hn_jobs_scraper import HNJobsScraper

        result = HNJobsScraper._extract_remote("We are hiring remote engineers worldwide")
        assert result is True

    @pytest.mark.asyncio
    async def test_extracts_salary(self):
        """Salary range should be parsed from description text."""
        from src.scrapers.hn_jobs_scraper import HNJobsScraper

        result = HNJobsScraper._extract_salary("Compensation: $120k - $160k per year")
        assert result is not None
        assert "120" in result or "160" in result

    @pytest.mark.asyncio
    async def test_handles_empty_hits(self):
        """Empty hits list should return empty jobs list."""
        from src.scrapers.hn_jobs_scraper import HNJobsScraper

        scraper = HNJobsScraper()
        mock_response = MagicMock()
        mock_response.json.return_value = {"hits": [], "nbHits": 0}
        mock_response.status_code = 200

        with patch.object(scraper, "_get", return_value=mock_response):
            jobs = await scraper._fetch_jobs()

        assert jobs == []
