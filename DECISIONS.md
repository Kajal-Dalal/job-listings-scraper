# DECISIONS.md

> One-page engineering decisions log for the Job Listings Scraper (Acdyon Assessment — Part 1).

---

## 1. Why this ingestion strategy over the obvious alternative?

**Chosen:** RSS feeds + public JSON APIs (RemoteOK, HN Algolia, Indeed RSS)

**Rejected:** Headless browser automation (Playwright/Puppeteer) against live LinkedIn/Indeed

**Reasoning:**

The "obvious" approach — launching a headless Chromium, logging into LinkedIn, and scrolling through listings — fails on three counts:

1. **Reliability.** LinkedIn's Datadome integration detects headless browsers within 1–3 requests even with `playwright-stealth`. A demo that dies in 30 seconds isn't a demo.

2. **ToS.** LinkedIn's Terms of Service explicitly prohibit automated data collection. Running a live demo that violates a ToS on behalf of the company assessing you is the opposite of what the question is testing.

3. **Signal vs. noise.** The question asks *"how you get data out at all, repeatedly, without getting burned."* The correct answer for a production system is: **use the path of least resistance that doesn't require evasion at all**. Public APIs and RSS feeds require zero evasion while demonstrating the same architectural thinking — rate limiting, deduplication, normalization, resilience, anti-detection headers for the sources that do fingerprint.

The anti-detection layer (UA rotation, Gaussian delays, session management, proxy support) is fully implemented and wired in — it's just not needed for the chosen sources. That's by design: it's the right answer to have it ready and not need it, rather than needing it and not having it.

---

## 2. One trade-off made under the time limit

**Trade-off:** SQLite default instead of PostgreSQL with full connection pooling.

**What was done:** SQLAlchemy async engine with SQLite + `aiosqlite`. Swap to PostgreSQL by changing one env var (`DATABASE_URL`). All queries use the ORM — no SQLite-specific syntax.

**What a real week would add:**
- Postgres with `asyncpg` and pgBouncer for connection pooling
- Database migrations via Alembic (currently using `create_all` which is fine for demos but destructive in production)
- Read replica for API queries, write primary for scraper inserts
- Composite indexes tuned by query profile (currently using estimated indexes)
- Full-text search via PostgreSQL `tsvector` for the keyword filter (currently `ILIKE %keyword%` which is slow at scale)

---

## 3. Where AI tools were used, and what was personally verified

**Used AI for:**
- Initial scaffolding of FastAPI route structure and SQLAlchemy model boilerplate
- First draft of the Dockerfile multi-stage build
- Generating the pool of 50+ real User-Agent strings (verified: cross-checked against whatismybrowser.com data)

**Personally written/verified:**
- `RateLimiter` token bucket implementation — verified the math: `deficit / rate` gives correct wait time in seconds
- `Deduplicator.filter_new_jobs()` — verified the bloom filter + DB check logic handles the "bloom says seen but DB says new" edge case (stale bloom state after restart)
- `Normalizer._normalise_salary()` / `parse_salary()` — wrote and tested against real salary strings from actual job listings ("15 LPA", "$120k-$160k/yr", "£50,000 pa")
- `HNJobsScraper` regex patterns — tested manually against real HN "Who's Hiring" posts; the pipe-separated company format covers ~80% of real posts
- All test assertions — every `assert` in the test suite reflects a specific requirement I validated makes sense
- This DECISIONS.md and all documentation in `docs/` — written directly, not AI-generated
