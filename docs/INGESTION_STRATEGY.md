# Ingestion Strategy

> How we pull data while staying under the radar, and what Plan B looks like when the primary approach fails.

---

## Source Selection Rationale

We target **three public, API/RSS-first sources** for the live demo:

| Source | Method | Auth Required | ToS Risk |
|---|---|---|---|
| **RemoteOK** | Public JSON API (`/api`) | None | None — documented public API |
| **HN Who's Hiring** | Algolia search API | None | None — Algolia API is public |
| **Indeed** | Public RSS feed | None | Low — RSS is publicly indexed |

### Why not LinkedIn/Glassdoor?

These platforms actively defend against scraping with:
1. Login walls for most content
2. Datadome/Cloudflare Bot Management on every page
3. CAPTCHA on any suspicious automation signal
4. Terms of Service that explicitly prohibit scraping

Breaching their ToS on a live demo would create liability for the candidate and the company. The assessment itself scopes the demo to "one low-risk source" — our design exceeds that scope safely.

---

## Request Flow

```
Scheduler triggers (every 60 min)
          │
          ▼
IngestionPipeline.run()
          │
          ├── asyncio.Semaphore(max=3) ──► 3 scrapers run in parallel
          │
          ├── RemoteOKScraper._fetch_jobs()
          │       │
          │       ├── RateLimiter.wait("remoteok.com")      ← Gaussian delay 2–8s
          │       ├── SessionManager.create_identity()       ← Fresh UA + headers
          │       ├── httpx.AsyncClient.get(REMOTEOK_URL)   ← HTTP/2, anti-detect headers
          │       └── Parse JSON → List[RawJobData]
          │
          ├── HNJobsScraper._fetch_jobs()
          │       └── (same flow, Algolia API)
          │
          └── IndeedRssScraper._fetch_jobs()
                  └── (same flow, RSS feed)

          │
          ▼
Normalizer.normalize_many(raw_jobs)
          │
          ▼
Deduplicator.filter_new_jobs(jobs, repo)
     ├── Bloom filter check  ←  O(1), no DB hit for known duplicates
     └── DB hash check       ←  Only for bloom-filter misses
          │
          ▼
JobRepository.bulk_create(new_jobs)
          │
          ▼
ScrapeRunRepository.update_finished(stats)
          │
          ▼
Deduplicator.persist()  ←  Bloom filter saved to disk
```

---

## Rotation and Pacing

### User-Agent Rotation
- 50+ real browser UA strings (Chrome 65%, Firefox 20%, Safari 10%, Edge 5%)
- Each request gets a fresh UA from the weighted pool
- Per-domain usage tracking avoids repeating the same UA on the same domain within a short window

### Rate Limiting (Token Bucket + Gaussian Jitter)
```
Per-domain token bucket:
  capacity = 5 tokens
  refill rate = 0.5 tokens/sec (1 request every 2 seconds minimum)

Human-like delay added on top:
  mean = (min_delay + max_delay) / 2 = 5 seconds
  std_dev = (max_delay - min_delay) / 4 = 1.5 seconds
  actual_delay ~ N(5, 1.5), clamped to [2, 8] seconds
```

Real human browsing averages about 3–10 seconds between page loads. Our distribution sits comfortably in that range.

### Backoff on Rate Limiting
When a source returns 429 or 503:
```
attempt 0: wait 2^1 = 2 + jitter(0–0.6) seconds
attempt 1: wait 2^2 = 4 + jitter(0–1.2) seconds
attempt 2: wait 2^3 = 8 + jitter(0–2.4) seconds
...
max cap: 120 seconds
```

---

## Session and Identity Management

Each scrape run creates a fresh `BrowserIdentity`:
- New `httpx.AsyncClient` (new TLS session, empty cookie jar)
- Fresh UA string sampled from the rotator
- Matching browser headers (Accept, Sec-Fetch-*, etc.)
- Referrer header set to simulate realistic navigation

Sessions are **not shared** across scrape runs. A session used at 10:00 AM is closed before the 11:00 AM run begins.

---

## Proxy Strategy

### Current (demo)
No proxies required for RemoteOK, HN Algolia, or Indeed RSS. These sources do not restrict by IP for normal access rates.

### Production escalation
If a source begins IP-blocking (indicated by repeated 403/429 after backoff):

1. **Rotate through proxy pool** — configure `PROXY_LIST` in `.env` with a comma-separated list of residential proxy URLs.
2. **ProxyManager** selects proxies weighted by success rate, auto-removes dead proxies.
3. **Health check** runs periodically to verify proxy pool health.

```env
PROXY_LIST=http://user:pass@proxy1.example.com:8080,socks5://proxy2.example.com:1080
```

---

## Plan B: Escalation Path

If the primary approach (RSS/public API) is blocked or removed:

| Escalation Level | Approach | Complexity | ToS Risk |
|---|---|---|---|
| 1 (current) | RSS feeds + public APIs | Low | None |
| 2 | Google Jobs RSS (public aggregator) | Low | None |
| 3 | Playwright with `playwright-stealth` + residential proxies | High | Medium |
| 4 | Third-party job data API (Adzuna, RapidAPI Jobs) | Low | None |

For this project, Level 1 is used for the live demo. Level 4 is the recommended path for production use at scale.

---

## Cold Start vs. Warm Operation

**Cold start (first run):**
- Bloom filter is empty — all jobs will trigger a DB check
- DB is empty — all jobs are inserted as new
- Expect higher latency on first run due to full DB writes

**Warm operation (subsequent runs):**
- Bloom filter loaded from `jobs_bloom.pkl` — most duplicates caught before DB hit
- Only truly new jobs reach the DB insert path
- Typical run: ~90% of fetched jobs are duplicates, bloom filter handles them in microseconds

---

## Resilience

See `RESILIENCE.md` for full details on what happens when a source fails mid-run.
