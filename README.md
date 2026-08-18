# Job Listings Scraper

<div align="center">

**Production-grade async job scraper — anti-detection, circuit breakers, event bus, REST API**

*Acdyon Technologies Engineering Assessment — Part 1*

**🌐 Live Demo:** https://job-listings-scraper-awj6.onrender.com/docs

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/Tests-40%20passing-brightgreen?style=flat&logo=pytest)](tests/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat&logo=docker&logoColor=white)](Dockerfile)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat)](LICENSE)

</div>

---

## What This Does

Pulls job listings from public sources — **RemoteOK**, **Hacker News Who's Hiring** (Algolia), and **Indeed RSS** — without getting blocked. Normalises, deduplicates, and exposes them via a typed REST API.

The interesting part isn't what happens when it works. It's what happens when a source starts blocking mid-run:

- **Circuit breaker** trips → that source fails fast, other scrapers continue unaffected
- **Bloom filter** catches 90%+ of duplicates before a DB round-trip
- **Gaussian request timing** means no two requests wait the same duration
- **Event bus** publishes `ScrapeCompletedEvent` → metrics update without touching scraper code

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Scheduler (APScheduler)                       │
│                         runs every 60 minutes                        │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  IngestionPipeline │  asyncio.gather — max 3 concurrent
                    └──┬────────┬───────┘
                       │        │        │
              ┌────────▼─┐ ┌───▼────┐ ┌─▼──────────┐
              │ RemoteOK │ │HN Jobs │ │ Indeed RSS │
              │ Scraper  │ │Scraper │ │  Scraper   │
              └────┬─────┘ └───┬────┘ └─────┬──────┘
                   │           │             │
                   └───────────┴─────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   Anti-Detection    │
                    │  ┌───────────────┐  │
                    │  │ UA Rotator    │  │  50+ real browser UAs, weighted
                    │  │ Rate Limiter  │  │  Token bucket + Gaussian delay
                    │  │ Session Mgr   │  │  Per-run browser identity
                    │  │ Circuit Break │  │  CLOSED → OPEN → HALF_OPEN
                    │  └───────────────┘  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │      Pipeline       │
                    │  Normalizer         │  Salary, dates, HTML, tags
                    │  Deduplicator       │  Bloom filter + SHA-256 hash
                    │  Event Bus          │  Publishes domain events
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   SQLite / Postgres  │
                    │   (async SQLAlchemy) │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │    FastAPI REST API  │
                    │  + Prometheus /metrics│
                    └─────────────────────┘
```

---

## Key Design Decisions

| Decision | Why |
|---|---|
| RSS + public APIs over headless browser | A demo that gets blocked in 30s isn't a demo. Reliability > cleverness. |
| Circuit breaker per source | One blocked source should never kill the pipeline |
| Bloom filter deduplication | 90%+ of fetched jobs are duplicates — O(1) check, no DB round-trip |
| Gaussian request timing | Fixed delays are a bot fingerprint. Humans don't wait exactly 2.000s |
| Event bus for side effects | Scrapers don't know about metrics or logging — events handle it |
| Repository pattern | Business logic never writes raw SQL — every query is tested independently |
| Cursor pagination | Offset breaks when live data arrives. Cursors give stable pages |

Full reasoning in [`DECISIONS.md`](DECISIONS.md).

---

## Quick Start

### Docker (one command)

```bash
git clone https://github.com/Kajal-Dalal/job-listings-scraper
cd job-listings-scraper
cp .env.example .env

docker-compose up -d
```

| Service | URL |
|---|---|
| **API + Swagger** | http://localhost:8000/docs |
| **Prometheus** | http://localhost:9090 |
| **Grafana** | http://localhost:3000 |

### Local

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
# source .venv/bin/activate                       # Linux/Mac

pip install -r requirements.txt
cp .env.example .env

python scripts/seed_db.py          # Seed sample jobs
python -m uvicorn src.main:app --reload
```

Open **http://localhost:8000/docs** — full interactive API.

---

## API Reference

### Jobs

```bash
# List jobs — paginated, filterable
GET /api/v1/jobs?keyword=python&remote_only=true&salary_min=100000

# Cursor pagination — stable for live data
GET /api/v1/jobs/cursor?page_size=20
GET /api/v1/jobs/cursor?cursor=<next_cursor_token>

# Platform statistics
GET /api/v1/jobs/stats

# Single job
GET /api/v1/jobs/{id}
```

**Filters:** `source`, `location`, `keyword`, `remote_only`, `salary_min`, `page`, `page_size`, `order_by`

**Sort options:** `scraped_at_desc` (default) · `scraped_at_asc` · `salary_desc` · `title_asc`

### System

```bash
GET /health          # Full health: DB + scheduler + circuit breakers
GET /health/live     # Kubernetes liveness probe
GET /health/ready    # Kubernetes readiness probe
GET /metrics         # Prometheus metrics
```

### Scraper Management *(API key required)*

```bash
# Trigger immediate scrape
POST /api/v1/scrape/trigger
     -H "X-API-Key: your-key"

# Recent run history
GET /api/v1/scrape/status

# Source stats
GET /api/v1/sources
```

### Sample Responses

<details>
<summary><code>GET /api/v1/jobs</code></summary>

```json
{
  "items": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "source": "remoteok",
      "title": "Senior Python Developer",
      "company": "RemoteFirst Inc",
      "location": "Worldwide",
      "remote": true,
      "salary_min": 90000,
      "salary_max": 140000,
      "salary_currency": "USD",
      "url": "https://remoteok.com/remote-jobs/python-developer",
      "tags": ["python", "django", "aws"],
      "scraped_at": "2024-01-15T10:05:32",
      "posted_at": "2024-01-14T08:00:00"
    }
  ],
  "total": 247,
  "page": 1,
  "page_size": 20,
  "total_pages": 13,
  "has_next": true,
  "has_prev": false
}
```
</details>

<details>
<summary><code>GET /health</code></summary>

```json
{
  "status": "ok",
  "db": "ok",
  "scheduler": {
    "enabled": true,
    "running": false,
    "interval_minutes": 60,
    "last_run": "2024-01-15T10:00:00",
    "next_run": "2024-01-15T11:00:00",
    "run_count": 12
  },
  "circuit_breakers": [
    { "name": "remoteok",  "state": "closed", "failure_count": 0 },
    { "name": "hn_jobs",   "state": "closed", "failure_count": 0 },
    { "name": "indeed_rss","state": "closed", "failure_count": 0 }
  ],
  "uptime_seconds": 3600.5,
  "version": "2.0.0"
}
```
</details>

---

## Anti-Detection Layer

The problem with most scrapers: they're trivially detectable.

| Signal | How Bots Get Caught | How This Design Avoids It |
|---|---|---|
| TLS fingerprint | Python's TLS stack ≠ Chrome | `httpx` with HTTP/2 — closer to real browser profile |
| User-Agent | `python-httpx/0.27` | 50+ real Chrome/Firefox/Safari/Edge UAs, weighted rotation |
| Request timing | Exactly 2.000s between every request | Gaussian distribution N(5s, 1.5s) — human-like variance |
| Header mismatch | No `Sec-Fetch-*` headers | Matching browser headers per UA family |
| Session tracking | New session per request, no cookies | `SessionManager` — one identity per scrape run with cookie jar |
| IP reputation | Datacenter IPs flagged | `ProxyManager` — residential proxy pool with health checks |
| Burst patterns | 100 requests in 10 seconds | Token bucket caps burst; semaphore limits concurrency to 3 |

Full analysis: [`docs/DETECTION_SURFACE.md`](docs/DETECTION_SURFACE.md)

---

## Resilience

What happens when things go wrong:

| Failure | Response |
|---|---|
| Source returns 429 | Exponential backoff: 2s → 4s → 8s + jitter; then circuit opens |
| Circuit breaker OPEN | Source skipped, other scrapers continue, auto-retry after 2 min |
| Source markup changes | Per-entry try/except; partial results still saved |
| DB write fails | Session rolled back; `ScrapeRun` marked `failed`; next run retries |
| Empty response | Logged as warning, `jobs_found=0`, pipeline continues |
| Process restart | Bloom filter reloaded from disk; scheduler restarts automatically |

Full details: [`docs/RESILIENCE.md`](docs/RESILIENCE.md)

---

## Configuration

```env
# Core
API_KEY=your-secret-key
DATABASE_URL=sqlite+aiosqlite:///./jobs.db   # or postgresql+asyncpg://...

# Scraper behaviour
SCRAPE_INTERVAL_MINUTES=60
MAX_CONCURRENT_SCRAPERS=3
MIN_DELAY_SECONDS=2
MAX_DELAY_SECONDS=8

# Sources (enable/disable individually)
ENABLED_SOURCES=remoteok,hn_jobs,indeed_rss

# Proxies (optional — comma-separated)
PROXY_LIST=http://proxy1:8080,socks5://proxy2:1080

# Observability
LOG_FORMAT=json        # json (prod) or console (dev)
LOG_LEVEL=INFO
ENABLE_METRICS=true
```

See [`.env.example`](.env.example) for all options.

---

## Observability

Every scrape run emits:

**Structured logs** (JSON in production)
```json
{
  "event": "scraper_finished",
  "source": "remoteok",
  "jobs_found": 98,
  "duration_seconds": 14.2,
  "correlation_id": "a3f2-...",
  "timestamp": "2024-01-15T10:00:14Z"
}
```

**Prometheus metrics** at `/metrics`
```
scraper_jobs_total{source="remoteok", status="new"} 12
scraper_jobs_total{source="remoteok", status="duplicate"} 86
scraper_duration_seconds{source="remoteok"} 14.2
active_jobs_total 247
api_requests_total{endpoint="/api/v1/jobs", method="GET", status_code="200"} 503
```

**Distributed tracing** — every request gets `X-Correlation-ID` in response headers, propagated to all log lines.

---

## Tests

```bash
# Run all tests
python -m pytest tests/ -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=html

# Specific modules
python -m pytest tests/test_scrapers/ -v
python -m pytest tests/test_pipeline/ -v
python -m pytest tests/test_api/ -v
```

```
40 passed in 2.7s
├── test_scrapers/test_rate_limiter.py    12 tests  (token bucket, backoff, domain isolation)
├── test_scrapers/test_rss_scraper.py     13 tests  (RSS, RemoteOK, HN parsers — mocked HTTP)
└── test_pipeline/test_deduplicator.py    15 tests  (bloom filter, normalizer, hash consistency)
```

---

## Project Structure

```
.
├── src/
│   ├── main.py                        # FastAPI app + lifespan hooks
│   ├── config/settings.py             # Pydantic settings (all from env vars)
│   ├── anti_detection/
│   │   ├── user_agent_rotator.py      # 50+ real UAs, weighted Chrome/Firefox/Safari/Edge
│   │   ├── rate_limiter.py            # Token bucket + Gaussian human-like delay
│   │   ├── session_manager.py         # Per-run browser identity + cookie jar
│   │   └── proxy_manager.py           # Proxy pool, health checks, weighted selection
│   ├── scrapers/
│   │   ├── base_scraper.py            # Abstract base: retry + circuit breaker + events
│   │   ├── remoteok_scraper.py        # RemoteOK public API
│   │   ├── hn_jobs_scraper.py         # HN Who's Hiring (Algolia API)
│   │   ├── indeed_public.py           # Indeed public RSS
│   │   └── rss_scraper.py             # Generic RSS/Atom
│   ├── pipeline/
│   │   ├── ingestion.py               # Orchestration: parallel scrape → normalise → dedup → persist
│   │   ├── normalizer.py              # Salary parsing, date parsing, HTML strip
│   │   ├── deduplicator.py            # Bloom filter + SHA-256 DB hash
│   │   └── scheduler.py               # APScheduler with Prometheus gauge
│   ├── storage/
│   │   ├── database.py                # Async SQLAlchemy engine (SQLite/Postgres)
│   │   ├── models.py                  # JobListing + ScrapeRun ORM models
│   │   └── repository.py              # Repository pattern — no raw SQL in business logic
│   ├── api/
│   │   ├── middleware.py              # Correlation ID, security headers, logging
│   │   ├── schemas.py                 # Pydantic v2 response schemas
│   │   └── routes/
│   │       ├── jobs.py                # GET /jobs, /jobs/cursor, /jobs/stats, /jobs/{id}
│   │       ├── scraper.py             # POST /scrape/trigger, GET /sources
│   │       └── health.py              # GET /health, /health/live, /health/ready
│   ├── monitoring/
│   │   ├── logger.py                  # structlog — JSON (prod) / console (dev)
│   │   └── metrics.py                 # Prometheus counters, gauges, histograms
│   └── utils/
│       ├── circuit_breaker.py         # CLOSED/OPEN/HALF_OPEN state machine
│       ├── events.py                  # Async event bus + domain events
│       ├── pagination.py              # Cursor encoding/decoding
│       ├── retry.py                   # RetryConfig + is_retryable_exception
│       └── validators.py              # Salary parser, HTML stripper, URL validator
├── tests/
│   ├── conftest.py                    # Fixtures: in-memory DB, test client, sample data
│   ├── test_scrapers/                 # Scraper unit tests (all HTTP mocked)
│   ├── test_api/                      # API endpoint integration tests
│   └── test_pipeline/                 # Deduplicator + Normalizer unit tests
├── docs/
│   ├── DETECTION_SURFACE.md           # TLS, HTTP/2, UA, timing fingerprints + mitigations
│   ├── INGESTION_STRATEGY.md          # Source selection, request flow, Plan B
│   ├── RESILIENCE.md                  # 7 failure modes + mitigations
│   └── ETHICS_AND_LIMITS.md           # ToS analysis, technical + personal limits
├── scripts/
│   └── seed_db.py                     # Seed DB with 8 sample jobs for demo
├── docker/prometheus.yml
├── DECISIONS.md                       # 1-page engineering decisions (assessment requirement)
├── ARCHITECTURE.md                    # Full architecture with ASCII diagrams
├── docker-compose.yml                 # App + Prometheus + Grafana
├── Dockerfile                         # Multi-stage: builder → runtime (non-root user)
└── .env.example                       # All configuration options documented
```

---

## Documentation

| Document | What's Inside |
|---|---|
| [`DECISIONS.md`](DECISIONS.md) | Why RSS over headless browser; trade-offs made; AI tool disclosure |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Full system architecture with ASCII diagrams and data flow |
| [`docs/DETECTION_SURFACE.md`](docs/DETECTION_SURFACE.md) | Every bot detection vector + how this design addresses each |
| [`docs/INGESTION_STRATEGY.md`](docs/INGESTION_STRATEGY.md) | Request flow, rotation strategy, Plan B escalation path |
| [`docs/RESILIENCE.md`](docs/RESILIENCE.md) | 7 failure modes (429, empty response, DB failure, markup change...) |
| [`docs/ETHICS_AND_LIMITS.md`](docs/ETHICS_AND_LIMITS.md) | ToS analysis per source, personal + technical limits |

---

<div align="center">
<sub>Built for Acdyon Technologies Engineering Assessment · Part 1 · 2026</sub>
</div>
