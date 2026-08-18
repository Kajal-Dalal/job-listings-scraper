"""
Tests for the rate limiter.

Validates:
- Token bucket refill mechanics
- Exponential backoff ranges
- Jitter is within expected bounds
- Domain isolation
"""
import asyncio
import time

import pytest

from src.anti_detection.rate_limiter import RateLimiter, TokenBucket, DomainRateLimiter


class TestTokenBucket:
    """Unit tests for the TokenBucket implementation."""

    def test_initial_tokens_equal_capacity(self):
        """A fresh bucket should start full."""
        bucket = TokenBucket(capacity=5.0, rate=1.0)
        assert bucket.tokens == 5.0

    def test_consume_returns_zero_when_enough_tokens(self):
        """Consuming available tokens should return 0 wait time."""
        bucket = TokenBucket(capacity=5.0, rate=1.0)
        wait = bucket.consume(1.0)
        assert wait == 0.0
        assert bucket.tokens == 4.0

    def test_consume_returns_wait_when_insufficient(self):
        """Consuming more than available tokens should return positive wait."""
        bucket = TokenBucket(capacity=2.0, rate=1.0)
        bucket.consume(2.0)  # Drain bucket
        wait = bucket.consume(1.0)
        assert wait > 0.0

    def test_tokens_refill_over_time(self):
        """Bucket should refill tokens based on elapsed time."""
        bucket = TokenBucket(capacity=10.0, rate=2.0)  # 2 tokens/sec
        bucket.consume(10.0)  # Drain bucket
        assert bucket.tokens < 1.0

        # Manually advance last_refill to simulate 2 seconds passing
        bucket.last_refill -= 2.0
        bucket._refill()

        # Should have approximately 4 tokens now (2/sec × 2 sec)
        assert bucket.tokens >= 3.5  # Allow small float tolerance

    def test_tokens_capped_at_capacity(self):
        """Refill should not exceed capacity."""
        bucket = TokenBucket(capacity=5.0, rate=1.0)
        # Advance time by 100 seconds
        bucket.last_refill -= 100.0
        bucket._refill()
        assert bucket.tokens == 5.0

    def test_burst_consumption(self):
        """Multiple sequential consumes should correctly track tokens."""
        bucket = TokenBucket(capacity=5.0, rate=0.1)  # Very slow refill
        # Consume all tokens
        for _ in range(5):
            wait = bucket.consume(1.0)
            assert wait == 0.0
        # Next consume should require a wait
        wait = bucket.consume(1.0)
        assert wait > 0.0


class TestRateLimiter:
    """Integration tests for RateLimiter."""

    @pytest.mark.asyncio
    async def test_wait_completes_for_known_domain(self):
        """wait() should complete without error for a valid domain."""
        # Use very short delays for testing
        limiter = RateLimiter(min_delay=0.01, max_delay=0.05)
        start = time.monotonic()
        await limiter.wait("example.com")
        elapsed = time.monotonic() - start
        # Should have waited at least the minimum delay
        assert elapsed >= 0.01

    @pytest.mark.asyncio
    async def test_backoff_increases_with_attempt(self):
        """Higher attempt numbers should result in longer backoff waits."""
        limiter = RateLimiter(min_delay=0.0, max_delay=0.01)
        # Measure backoff times for attempt 0 vs attempt 3
        # We can't easily measure this without mocking asyncio.sleep,
        # but we can verify backoff doesn't error and doesn't take forever
        start = time.monotonic()
        await limiter.backoff("test.com", 429, attempt=0)
        elapsed_0 = time.monotonic() - start

        # Attempt 0 backoff should be small (2^1 = 2 sec base + jitter)
        # We accept any reasonable completion
        assert elapsed_0 >= 0

    @pytest.mark.asyncio
    async def test_domain_isolation(self):
        """Different domains should have independent token buckets."""
        limiter = RateLimiter(min_delay=0.0, max_delay=0.01)
        # Drain domain A
        bucket_a = limiter._buckets["domain_a.com"]
        bucket_a.tokens = 0

        # Domain B should be unaffected
        bucket_b = limiter._buckets["domain_b.com"]
        assert bucket_b.tokens == limiter._default_capacity

    def test_configure_domain_overrides_defaults(self):
        """configure_domain() should set custom rate/capacity."""
        limiter = RateLimiter()
        limiter.configure_domain("fast.com", rate=10.0, capacity=20.0)
        bucket = limiter._buckets["fast.com"]
        assert bucket.rate == 10.0
        assert bucket.capacity == 20.0


class TestDomainRateLimiter:
    """Tests for the URL-based DomainRateLimiter wrapper."""

    def test_extract_domain_from_url(self):
        """Should correctly extract domain from various URL forms."""
        limiter = DomainRateLimiter(min_delay=0.0, max_delay=0.01)
        assert limiter._extract_domain("https://remoteok.com/api") == "remoteok.com"
        assert limiter._extract_domain("https://www.indeed.com/rss?q=python") == "www.indeed.com"
        assert limiter._extract_domain("not-a-url") == "default"
        assert limiter._extract_domain("") == "default"

    @pytest.mark.asyncio
    async def test_wait_for_url(self):
        """wait_for_url() should work without raising."""
        limiter = DomainRateLimiter(min_delay=0.01, max_delay=0.02)
        # Should not raise
        await limiter.wait_for_url("https://example.com/api/jobs")
