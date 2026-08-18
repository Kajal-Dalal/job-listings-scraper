# Architecture

> System design overview for the Job Listings Scraper.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Ingestion Layer                          │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ RemoteOK API │  │  HN Algolia  │  │   Indeed RSS Feed     │ │
│  │  Scraper     │  │  Scraper     │  │   Scraper             │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘ │
│         │                  │                      │             │
│         └──────────────────┴──────────────────────┘             │
│                             │                                   │
│                    ┌────────▼────────┐                          │
│                    │ Anti-Detection  │                          │
│                    │ ┌─────────────┐ │                          │
│                    │ │ UA Rotator  │ │ 50+ real browser UAs     │
│                    │ │ Rate Limiter│ │ Token bucket + Gaussian  │
│                    │ │ Session Mgr │ │ Per-run browser identity │
│                    │ │ Proxy Mgr   │ │ Weighted proxy selection │
│                    │ └─────────────┘ │                          │
│                    └────────┬────────┘                          │
└─────────────────────────────┼───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Processing Pipeline                        │
│                                                                 │
│  RawJobData ──► Normalizer ──► Deduplicator ──► JobListing ORM │
│                  │                │                             │
│                  │ Salary parse   │ Bloom filter (fast)        │
│                  │ Date parse     │ DB hash check (accurate)   │
│                  │ HTML strip     │                             │
│                  │ Tag clean      │                             │
│                  └────────────────┘                             │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                        Storage Layer                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  SQLAlchemy Async (SQLite dev / PostgreSQL prod)         │   │
│  │                                                          │   │
│  │  job_listings table          scrape_runs table          │   │
│  │  ─────────────────           ──────────────────         │   │
│  │  id (UUID PK)                id (UUID PK)               │   │
│  │  source                      source                     │   │
│  │  title, company              started_at                 │   │
│  │  location, remote            finished_at                │   │
│  │  salary_min, salary_max      status                     │   │
│  │  description, url            jobs_found/new/dup         │   │
│  │  tags (JSON)                 error_message              │   │
│  │  hash (SHA-256, unique)                                 │   │
│  │  scraped_at, posted_at                                  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                          API Layer                              │
│                                                                 │
│  FastAPI (async) + Uvicorn                                      │
│                                                                 │
│  GET  /health                   System health check            │
│  GET  /api/v1/jobs              Paginated, filtered job list   │
│  GET  /api/v1/jobs/{id}         Single job detail              │
│  POST /api/v1/scrape/trigger    Manual scrape (API key)        │
│  GET  /api/v1/scrape/status     Scrape run history             │
│  GET  /api/v1/sources           Source status + stats         │
│  GET  /metrics                  Prometheus metrics             │
│                                                                 │
│  Middleware:                                                    │
│  ├── CORS                                                       │
│  ├── Request ID injection (X-Request-ID)                        │
│  └── Structured request/response logging                        │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                      Observability                              │
│                                                                 │
│  structlog → JSON (production) / Console (development)         │
│  prometheus-client → /metrics endpoint                          │
│  Grafana → dashboards (via docker-compose)                      │
│                                                                 │
│  Key metrics:                                                   │
│  - scraper_jobs_total{source, status}                           │
│  - scraper_duration_seconds{source}                             │
│  - scraper_errors_total{source, error_type}                     │
│  - api_requests_total{endpoint, method, status_code}            │
│  - active_jobs_total (gauge)                                    │
│  - scheduler_next_run_timestamp_seconds                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

### Scrapers (`src/scrapers/`)
Each scraper inherits `BaseScraper` which provides:
- HTTP GET with anti-detection headers via `SessionManager`
- Automatic rate limiting via `DomainRateLimiter`
- Retry with tenacity (3 attempts, exponential backoff)
- Prometheus metrics emission
- Structured logging

Concrete scrapers implement only `_fetch_jobs()` — the data extraction logic.

### Anti-Detection (`src/anti_detection/`)
Four independent modules:
- `UserAgentRotator`: weighted UA pool with matching browser headers
- `RateLimiter`: per-domain token bucket + Gaussian human-like delay
- `SessionManager`: per-run browser identities with fresh cookie jars
- `ProxyManager`: weighted proxy selection with health monitoring

### Pipeline (`src/pipeline/`)
- `IngestionPipeline`: orchestrates parallel scraping, normalisation, dedup, persistence
- `Normalizer`: schema normalisation (salary parsing, date parsing, HTML strip, tag clean)
- `Deduplicator`: Bloom filter + DB hash check for fast duplicate detection
- `Scheduler`: APScheduler wrapper with Prometheus gauge updates

### Storage (`src/storage/`)
Repository pattern: business logic never writes raw SQL. Two repositories:
- `JobRepository`: CRUD + filtered pagination for `JobListing`
- `ScrapeRunRepository`: audit log for every scrape run

### API (`src/api/`)
FastAPI routes with Pydantic v2 schemas separate from ORM models.
Write endpoints protected by `X-API-Key` header.

---

## Data Flow: Single Scrape Run

```
APScheduler fires
      │
      ▼
IngestionPipeline.run()
      │
      ├─ asyncio.gather() ─┬─ RemoteOKScraper.scrape()
      │   (Semaphore=3)    ├─ HNJobsScraper.scrape()
      │                    └─ IndeedRssScraper.scrape()
      │
      ▼ (all results collected)
      │
      ├─ for each ScraperResult:
      │     Normalizer.normalize_many(raw_jobs)
      │           │
      │           ▼
      │     Deduplicator.filter_new_jobs(jobs, repo)
      │           │ Bloom filter hit?  → skip (dup)
      │           │ Bloom miss → DB exists? → skip (dup)
      │           │             DB miss → new job
      │           ▼
      │     JobRepository.bulk_create(new_jobs)
      │     ScrapeRunRepository.update_finished(stats)
      │
      ▼
Deduplicator.persist()  ← Bloom filter to disk
metrics.set_active_jobs(total)
```

---

## Deployment Architecture

```
Internet
    │
    ▼
[Render / Railway / VPS]
    │
    ├── Docker container: job-scraper-app:8000
    │       └── Uvicorn (1 worker) + FastAPI
    │
    ├── Docker container: prometheus:9090
    │       └── Scrapes /metrics every 15s
    │
    └── Docker container: grafana:3000
            └── Dashboards from Prometheus
```

SQLite database is persisted via a Docker volume (`./data/jobs.db`).
Bloom filter is persisted to `./data/jobs_bloom.pkl`.

For horizontal scaling, replace SQLite with PostgreSQL + add Redis for distributed Bloom filter.
