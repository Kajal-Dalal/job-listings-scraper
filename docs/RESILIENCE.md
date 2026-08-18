# Resilience Design

> What keeps the pipeline running instead of silently failing.

---

## Failure Modes and Mitigations

### 1. Source Returns Empty Response

**Scenario:** RemoteOK returns an empty array `[]` or an empty RSS feed.

**Detection:** Each scraper checks for empty results explicitly and logs a warning:
```python
if not feed.entries:
    log.warning("rss_no_entries", url=self._feed_url)
    return []
```

**Mitigation:**
- Empty result is NOT treated as an error — it's a valid (if unusual) state.
- `ScrapeRun` is recorded with `jobs_found=0` and `status=success`.
- Scheduler continues as normal; next run will attempt again.
- Prometheus counter `scraper_jobs_total{status="new"}` will show zero, which will alert on-call if configured.

---

### 2. Source Changes Its Markup / Schema Overnight

**Scenario:** RemoteOK renames `position` field to `job_title`. IndeedRSS changes title format from `"Title - Company"` to `"Title @ Company"`.

**Detection:**
- Normalizer receives `RawJobData` with empty `title` → logs a warning and skips the entry.
- If >80% of entries from a source are skipped in one run, the scrape run is marked `partial`.

**Mitigation:**
- Parsers use `.get()` with fallbacks for all optional fields:
  ```python
  title = (data.get("position") or data.get("title") or "").strip()
  ```
- `Normalizer.normalize_many()` wraps each entry in try/except and logs individual failures without aborting the entire run.
- Structural hash of a sample response can be stored (future enhancement) to trigger an alert on schema changes.

---

### 3. HTTP Rate Limiting (429 / 503)

**Scenario:** Source temporarily rate-limits our IP.

**Detection:** `httpx.HTTPStatusError` with `status_code == 429`.

**Mitigation:**
1. Rate limiter immediately raises the error.
2. `tenacity` retries up to 3 times with exponential backoff + jitter.
3. Backoff waits: 2s → 4s → 8s (+ random jitter).
4. After 3 failures, the scraper marks itself `failed` and records the error in `ScrapeRun`.
5. Other scrapers in the pipeline continue unaffected.
6. Prometheus metric `rate_limiter_backoffs_total{domain=...,status_code=429}` is incremented for alerting.

---

### 4. Network Timeout

**Scenario:** DNS hangs, proxy drops connection, server takes >30 seconds to respond.

**Detection:** `httpx.TimeoutException` (includes connect, read, write, pool timeouts).

**Mitigation:**
- Default timeout: 30 seconds (configurable via `REQUEST_TIMEOUT_SECONDS`).
- `tenacity` retries timeout errors (classified as transient via `is_retryable_exception()`).
- After max retries, `ScraperResult.success = False` but other scrapers continue.

---

### 5. Database Write Failure

**Scenario:** SQLite is locked, disk full, or PostgreSQL connection dropped mid-run.

**Detection:** SQLAlchemy raises during `session.flush()` or `session.commit()`.

**Mitigation:**
- `Database.session()` context manager automatically rolls back on exception.
- `IngestionPipeline._persist_jobs()` catches the exception, records a `ScrapeRun` with `status=failed`, and re-raises.
- The exception is caught at the top-level `IngestionPipeline.run()`, which continues processing other scrapers.
- **Data integrity:** Because the session is rolled back, no partial job batches are committed. The next run will re-fetch and re-insert.

---

### 6. Scheduler Failure

**Scenario:** APScheduler throws during job execution, or the process restarts mid-scrape.

**Mitigation:**
- APScheduler is configured with `max_instances=1` — a new run never starts while one is in progress.
- On process restart, any `ScrapeRun` records stuck in `status=running` are orphaned (but don't block future runs).
- The scheduler restarts automatically on app startup via the lifespan hook.
- Bloom filter is persisted to disk after every successful run, so the dedup state survives restarts.

---

### 7. Partial Source Failures

**Scenario:** RemoteOK succeeds, HN Algolia times out, Indeed RSS is blocked.

**Mitigation:**
- Scrapers run concurrently via `asyncio.gather()` — one failure does not cancel the others.
- Each scraper produces an independent `ScraperResult`.
- Successful results are persisted; failed ones are logged and recorded.
- `IngestionResult.sources_run` lists which sources completed; errors list what failed.

```python
# From ingestion.py — failures don't propagate across scrapers
scrape_results: List[ScraperResult] = await asyncio.gather(
    *[self._run_single_scraper(s) for s in self._scrapers],
    return_exceptions=False,  # Each scraper handles its own exceptions
)
```

---

## Observability

All failure modes emit structured log events (JSON in production) and Prometheus metrics:

| Event | Log Event | Metric |
|---|---|---|
| Scrape success | `scraper_finished` | `scraper_runs_total{status="success"}` |
| Scrape failed | `scraper_failed` | `scraper_runs_total{status="failed"}` |
| Rate limited | `rate_limiter_backoff` | `rate_limiter_backoffs_total` |
| DB write error | `persist_failed` | `db_operations_total{status="error"}` |
| Parse error | `normalizer_error` | _(log only)_ |
| Empty source | `rss_no_entries` / `remoteok_unexpected_format` | _(log only)_ |

A Grafana dashboard (provisioned via `docker/grafana/provisioning/`) visualises these metrics in real time.

---

## What We Don't Protect Against (Current Scope)

| Scenario | Status | Notes |
|---|---|---|
| Bloom filter corruption | Unhandled | Falls back to fresh bloom filter on load failure |
| Poison data (XSS in job titles) | Partially | HTML stripped in normalizer; SQLAlchemy parameterises all queries |
| Source permanently removed | Unhandled | Manual intervention required to remove from `ENABLED_SOURCES` |
| Multi-instance race conditions | Unhandled | Current design is single-instance; Postgres advisory locks needed for multi-instance |
