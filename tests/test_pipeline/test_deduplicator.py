"""
Tests for the Deduplicator.

Validates:
- Hash computation consistency
- Bloom filter detects duplicates
- DB check overrides Bloom false positives
- Session hash cache clears correctly
- Bloom filter persists and loads from disk
"""
import hashlib
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.pipeline.deduplicator import Deduplicator
from src.storage.models import JobListing


def _make_job(url="https://example.com/1", title="Engineer", company="Corp") -> JobListing:
    key = f"{url.lower()}|{title.lower()}|{company.lower()}"
    return JobListing(
        id=str(uuid.uuid4()),
        source="test",
        title=title,
        company=company,
        url=url,
        remote=False,
        salary_currency="USD",
        hash=hashlib.sha256(key.encode()).hexdigest(),
    )


class TestDeduplicator:
    """Unit tests for the Deduplicator class."""

    def test_compute_hash_is_deterministic(self):
        """Same (url, title, company) should always produce the same hash."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_det.pkl"))
        h1 = dedup.compute_hash("https://example.com/1", "Engineer", "Corp")
        h2 = dedup.compute_hash("https://example.com/1", "Engineer", "Corp")
        assert h1 == h2

    def test_compute_hash_is_case_insensitive(self):
        """Hash should be the same regardless of input case."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_case.pkl"))
        h1 = dedup.compute_hash("HTTPS://EXAMPLE.COM/1", "ENGINEER", "CORP")
        h2 = dedup.compute_hash("https://example.com/1", "engineer", "corp")
        assert h1 == h2

    def test_compute_hash_differs_for_different_inputs(self):
        """Different jobs should produce different hashes."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_diff.pkl"))
        h1 = dedup.compute_hash("https://example.com/1", "Engineer", "Corp")
        h2 = dedup.compute_hash("https://example.com/2", "Manager", "OtherCorp")
        assert h1 != h2

    def test_is_probably_seen_returns_false_for_new_hash(self):
        """A hash never added to the bloom filter should return False."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_new.pkl"))
        assert dedup.is_probably_seen("never-seen-hash") is False

    def test_mark_seen_causes_is_probably_seen_to_return_true(self):
        """After marking a hash, is_probably_seen should return True."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_mark.pkl"))
        test_hash = "abc123def456"
        dedup.mark_seen(test_hash)
        assert dedup.is_probably_seen(test_hash) is True

    def test_session_hashes_cleared_after_reset(self):
        """reset_session() should clear the session hash cache."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_reset.pkl"))
        test_hash = "session-hash-xyz"

        # Add to session set (bypass bloom filter)
        dedup._session_hashes.add(test_hash)
        assert dedup.is_probably_seen(test_hash) is True

        dedup.reset_session()
        # Session hashes cleared — only bloom filter now
        # Since we didn't call mark_seen(), it's not in bloom filter either
        # Note: bloom filter might have false positives, so we check session set directly
        assert test_hash not in dedup._session_hashes

    def test_bloom_filter_persist_and_load(self):
        """Bloom filter should persist to disk and reload correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bloom_path = Path(tmpdir) / "test_bloom.pkl"
            dedup = Deduplicator(bloom_path=bloom_path)

            test_hash = "persistent-hash-12345"
            dedup.mark_seen(test_hash)
            dedup.persist()

            # Create new deduplicator with same path — should load persisted state
            dedup2 = Deduplicator(bloom_path=bloom_path)
            assert dedup2.is_probably_seen(test_hash) is True

    @pytest.mark.asyncio
    async def test_filter_new_jobs_returns_new_and_counts_duplicates(self):
        """filter_new_jobs should separate new from duplicate jobs."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_filter.pkl"))

        job_new = _make_job(url="https://example.com/new", title="New Job")
        job_dup = _make_job(url="https://example.com/dup", title="Duplicate Job")

        # Pre-mark the duplicate
        dedup.mark_seen(job_dup.hash)

        # Mock repo: job_new doesn't exist in DB, job_dup does
        mock_repo = MagicMock()
        mock_repo.exists_by_hash = AsyncMock(side_effect=lambda h: h == job_dup.hash)

        new_jobs, dup_count = await dedup.filter_new_jobs(
            [job_new, job_dup], mock_repo
        )

        assert len(new_jobs) == 1
        assert new_jobs[0].url == "https://example.com/new"
        assert dup_count == 1

    @pytest.mark.asyncio
    async def test_filter_new_jobs_all_new(self):
        """If all jobs are new, dup_count should be 0."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_allnew.pkl"))
        jobs = [
            _make_job(url=f"https://example.com/{i}", title=f"Job {i}")
            for i in range(3)
        ]

        mock_repo = MagicMock()
        mock_repo.exists_by_hash = AsyncMock(return_value=False)

        new_jobs, dup_count = await dedup.filter_new_jobs(jobs, mock_repo)
        assert len(new_jobs) == 3
        assert dup_count == 0

    @pytest.mark.asyncio
    async def test_filter_new_jobs_all_duplicates(self):
        """If all jobs are duplicates, new_jobs should be empty."""
        dedup = Deduplicator(bloom_path=Path("/tmp/test_bloom_alldups.pkl"))
        jobs = [
            _make_job(url=f"https://example.com/{i}", title=f"Job {i}")
            for i in range(3)
        ]
        # Mark all as seen
        for job in jobs:
            dedup.mark_seen(job.hash)

        mock_repo = MagicMock()
        mock_repo.exists_by_hash = AsyncMock(return_value=True)

        new_jobs, dup_count = await dedup.filter_new_jobs(jobs, mock_repo)
        assert len(new_jobs) == 0
        assert dup_count == 3


class TestNormalizer:
    """Tests for the Normalizer."""

    def test_normalizes_basic_job(self):
        """Should convert RawJobData to a JobListing ORM object."""
        from src.pipeline.normalizer import Normalizer
        from src.scrapers.base_scraper import RawJobData

        normalizer = Normalizer()
        raw = RawJobData(
            source="test",
            external_id="abc",
            title="Python Engineer",
            company="TechCorp",
            location="Remote",
            url="https://example.com/jobs/1",
            description="Great Python job.",
            salary_raw="$100,000 - $140,000",
            tags=["python", "django"],
            posted_at_raw="2024-01-15T10:00:00Z",
            remote=True,
        )
        job = normalizer.normalize(raw)

        assert job is not None
        assert job.title == "Python Engineer"
        assert job.company == "TechCorp"
        assert job.remote is True
        assert job.salary_min == 100000
        assert job.salary_max == 140000
        assert job.salary_currency == "USD"
        assert "python" in job.tags
        assert job.posted_at is not None
        assert len(job.hash) == 64  # SHA-256 hex digest

    def test_skips_job_with_empty_title(self):
        """Jobs with no title should be skipped (return None)."""
        from src.pipeline.normalizer import Normalizer
        from src.scrapers.base_scraper import RawJobData

        normalizer = Normalizer()
        raw = RawJobData(
            source="test", external_id="x", title="", company="Corp",
            location=None, url="https://example.com", description=None,
            salary_raw=None, tags=[],
        )
        assert normalizer.normalize(raw) is None

    def test_skips_job_with_empty_url(self):
        """Jobs with no URL should be skipped."""
        from src.pipeline.normalizer import Normalizer
        from src.scrapers.base_scraper import RawJobData

        normalizer = Normalizer()
        raw = RawJobData(
            source="test", external_id="x", title="Engineer", company="Corp",
            location=None, url="", description=None, salary_raw=None, tags=[],
        )
        assert normalizer.normalize(raw) is None

    def test_strips_html_from_description(self):
        """HTML tags in description should be stripped."""
        from src.pipeline.normalizer import Normalizer
        from src.scrapers.base_scraper import RawJobData

        normalizer = Normalizer()
        raw = RawJobData(
            source="test", external_id="x", title="Engineer", company="Corp",
            location=None, url="https://example.com/1",
            description="<p>We need a <b>Python</b> developer.</p>",
            salary_raw=None, tags=[],
        )
        job = normalizer.normalize(raw)
        assert job is not None
        assert "<p>" not in job.description
        assert "Python" in job.description

    def test_dedup_hash_is_stable(self):
        """Same job should produce the same hash each time."""
        from src.pipeline.normalizer import Normalizer
        from src.scrapers.base_scraper import RawJobData

        normalizer = Normalizer()
        raw = RawJobData(
            source="test", external_id="x", title="Engineer", company="Corp",
            location=None, url="https://example.com/1",
            description=None, salary_raw=None, tags=[],
        )
        job1 = normalizer.normalize(raw)
        job2 = normalizer.normalize(raw)
        assert job1.hash == job2.hash
