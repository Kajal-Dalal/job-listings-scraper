"""
Deduplication using both a Bloom filter (fast in-memory) and
a database hash store (persistent, authoritative).

Strategy:
1. Check Bloom filter — if probably not seen, proceed.
2. Check DB by hash — authoritative truth.
3. If new, insert into DB and add hash to Bloom filter.

The Bloom filter trades a tiny false-positive rate for speed:
jobs that are definitely new are inserted immediately without a DB round-trip.
"""
import hashlib
import os
import pickle
from pathlib import Path
from typing import List, Set

from pybloom_live import ScalableBloomFilter

from src.storage.models import JobListing
from src.storage.repository import JobRepository
from src.monitoring.logger import get_logger

log = get_logger(__name__)

_BLOOM_PERSIST_PATH = Path("./jobs_bloom.pkl")


class Deduplicator:
    """
    Manages deduplication of job listings.

    Uses a Scalable Bloom Filter for fast in-memory checks,
    with the database as the authoritative source of truth.
    """

    def __init__(
        self,
        bloom_path: Path = _BLOOM_PERSIST_PATH,
        bloom_capacity: int = 100_000,
        bloom_error_rate: float = 0.001,
    ):
        self._bloom_path = bloom_path
        self._bloom = self._load_or_create_bloom(
            bloom_capacity, bloom_error_rate
        )
        self._session_hashes: Set[str] = set()  # hashes seen this session

    def _load_or_create_bloom(
        self, capacity: int, error_rate: float
    ) -> ScalableBloomFilter:
        """Load persisted Bloom filter from disk or create new one."""
        if self._bloom_path.exists():
            try:
                with open(self._bloom_path, "rb") as f:
                    bloom = pickle.load(f)
                log.info(
                    "bloom_filter_loaded",
                    path=str(self._bloom_path),
                )
                return bloom
            except Exception as exc:
                log.warning(
                    "bloom_filter_load_failed",
                    path=str(self._bloom_path),
                    error=str(exc),
                )

        log.info(
            "bloom_filter_created",
            capacity=capacity,
            error_rate=error_rate,
        )
        return ScalableBloomFilter(
            mode=ScalableBloomFilter.LARGE_SET_GROWTH,
            error_rate=error_rate,
        )

    def persist(self) -> None:
        """Save the Bloom filter to disk for persistence across restarts."""
        try:
            with open(self._bloom_path, "wb") as f:
                pickle.dump(self._bloom, f)
            log.debug("bloom_filter_persisted", path=str(self._bloom_path))
        except Exception as exc:
            log.warning("bloom_filter_persist_failed", error=str(exc))

    def compute_hash(self, url: str, title: str, company: str) -> str:
        """Compute the canonical dedup hash for a job."""
        key = f"{url.lower().strip()}|{title.lower().strip()}|{company.lower().strip()}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def is_probably_seen(self, hash_value: str) -> bool:
        """
        Fast check: has this hash been seen before?
        May return False when the item is actually new (never False positive for fresh items).
        May return True for items not yet in DB if Bloom filter was pre-loaded.
        """
        return hash_value in self._bloom or hash_value in self._session_hashes

    def mark_seen(self, hash_value: str) -> None:
        """Add a hash to the Bloom filter and session set."""
        self._bloom.add(hash_value)
        self._session_hashes.add(hash_value)

    async def filter_new_jobs(
        self,
        jobs: List[JobListing],
        repo: JobRepository,
    ) -> tuple[List[JobListing], int]:
        """
        Filter out duplicate jobs.

        First does a fast Bloom filter pass, then verifies against DB
        for items that pass the Bloom filter.

        Returns:
            (new_jobs, duplicate_count)
        """
        new_jobs: List[JobListing] = []
        duplicate_count = 0

        for job in jobs:
            # Fast path: Bloom filter says "definitely not seen"
            if not self.is_probably_seen(job.hash):
                # Authoritative DB check
                exists = await repo.exists_by_hash(job.hash)
                if not exists:
                    new_jobs.append(job)
                    self.mark_seen(job.hash)
                else:
                    # Bloom filter missed this — add it for future queries
                    self.mark_seen(job.hash)
                    duplicate_count += 1
            else:
                duplicate_count += 1

        log.debug(
            "deduplicator_filtered",
            total=len(jobs),
            new=len(new_jobs),
            duplicates=duplicate_count,
        )
        return new_jobs, duplicate_count

    def reset_session(self) -> None:
        """Clear session-level hash cache (call at end of each scrape run)."""
        self._session_hashes.clear()
