# Detection Surface Analysis

> How anti-bot systems identify automated clients — and how this design addresses each signal.

---

## 1. TLS Fingerprinting (JA3 / JA3S)

**What it is:** Every TLS client handshake produces a fingerprint based on the ordered list of cipher suites, extensions, and elliptic curves offered. Cloudflare, Akamai, and Datadome compute a JA3 hash from this handshake and compare it against known bot fingerprints. Python's `urllib3`/`requests` produces a distinctive JA3 hash that differs from real browsers.

**How it gives you away:** Python's default TLS stack offers a different set of ciphers and extensions than Chrome or Firefox. A site can block your IP the moment the TLS fingerprint doesn't match any known real-browser profile.

**How this design addresses it:**
- Using `httpx` with `http2=True` produces an HTTP/2 + TLS profile closer to Chrome's.
- If stricter evasion is needed, `curl_cffi` (not yet in this stack) can be added to impersonate Chrome's exact TLS fingerprint.
- The session manager creates a new `httpx.AsyncClient` per identity — avoiding fingerprint leakage across sessions.

---

## 2. HTTP/2 Fingerprinting (HPACK / Pseudo-header Order)

**What it is:** HTTP/2 clients are identifiable by the order and weight of stream dependency frames, SETTINGS frame values, and HPACK header compression table seeding. Chrome's HTTP/2 fingerprint differs from Python's.

**How it gives you away:** Detecting `httpx` or `requests` at HTTP/2 level (different SETTINGS, header order) even if the UA string claims to be Chrome.

**How this design addresses it:**
- `httpx` with `http2=True` is used throughout — this is meaningfully closer to browser HTTP/2 behaviour than `requests` (which falls back to HTTP/1.1).
- Header order is set to match Chrome: `User-Agent`, `Accept`, `Accept-Encoding`, `Accept-Language` in that order — matching the output of our `UserAgentRotator`.

---

## 3. User-Agent / Header Fingerprinting

**What it is:** Missing, mismatched, or robot-order HTTP headers are an immediate signal. Chrome always sends `Sec-Fetch-*` headers. Python's `requests` sends none.

**How it gives you away:**
- Sending `User-Agent: python-httpx/0.27` directly.
- Sending headers in wrong order (Content-Type before User-Agent).
- Missing `Sec-Fetch-Dest`, `Sec-Fetch-Mode`, `Sec-Fetch-Site` headers.
- Mismatched Accept header (requesting JSON with a Firefox UA).

**How this design addresses it:**
- `UserAgentRotator` maintains 50+ real browser UA strings with matching `Accept`, `Accept-Encoding`, `Accept-Language`, `Sec-Fetch-*` header sets per browser family.
- Chrome UA strings are paired with Chrome-specific headers; Firefox with Firefox headers.
- `Accept-Language` is randomised from a pool of common locales.

---

## 4. Behavioral / Timing Analysis

**What it is:** Real humans don't make requests at exactly 1 request/second with zero variance. Sites log inter-request timing and flag clients with machine-precision intervals.

**How it gives you away:**
- Fixed delay between requests (e.g. exactly 2.000 seconds every time).
- Zero delay between requests (pure automation).
- Making 100 requests in 10 seconds (burst that no human achieves).

**How this design addresses it:**
- `RateLimiter` uses a **Gaussian distribution** to sample delay times between `min_delay` and `max_delay` (default 2–8 seconds). No two consecutive requests wait the same duration.
- Token bucket prevents accidental bursts even if the scheduler fires too often.
- Exponential backoff with **random jitter** on 429/503 avoids retry storms.

---

## 5. IP Reputation / Datacenter IP Detection

**What it is:** Sites maintain lists of known datacenter CIDR ranges (AWS, GCP, Azure, Hetzner, DigitalOcean). Requests from these IPs are immediately suspect. Some sites block them entirely.

**How it gives you away:**
- Running your scraper on Render, Railway, or a DigitalOcean VPS gives you a datacenter IP that is listed in MaxMind or similar.

**How this design addresses it:**
- `ProxyManager` supports rotating residential proxies (user-supplied via `PROXY_LIST` env var).
- Graceful degradation: if no proxies are configured, falls back to direct connection (acceptable for demo on low-risk sources like RemoteOK and HN).
- For production scraping of detection-heavy sites, residential proxy rotation (Bright Data, Oxylabs, etc.) would be wired in via `PROXY_LIST`.

---

## 6. Cookie / Session Tracking

**What it is:** Sites issue a tracking cookie on the first visit. If your next request doesn't carry that cookie (because you're a stateless scraper), or if you carry the same cookie across many IPs, you're flagged.

**How it gives you away:**
- Completely stateless requests with no cookies.
- Same cookie re-used across different IP addresses.
- Cookie expiry ignored (expired cookie re-sent).

**How this design addresses it:**
- `SessionManager` creates a `BrowserIdentity` per scrape run with its own `httpx.AsyncClient` and implicit cookie jar.
- Different scrape runs use different identities — cookies from one run don't leak into the next.
- `Referer` header chain is simulated: pages reference realistic entry points.

---

## 7. Headless Browser Detection

**What it is:** Sites detect Playwright/Puppeteer/Selenium through DOM properties: `navigator.webdriver`, `window.chrome` absence, canvas fingerprint, audio context fingerprint, missing `getBattery()` API, etc.

**Relevance to this project:** This design consciously avoids headless browsers entirely for the live demo. We use RSS/API endpoints which don't execute JavaScript, making these vectors irrelevant for our chosen sources.

**Design note:** If a headless browser were required (e.g. for sites with no API), mitigations include `playwright-stealth`, randomised canvas noise injection, and `rebrowser-patches`. These are documented in `INGESTION_STRATEGY.md` as the "Plan B" escalation path.

---

## Summary Table

| Detection Vector | Severity | Addressed By |
|---|---|---|
| TLS fingerprint (JA3) | High | httpx + HTTP/2 |
| HTTP/2 fingerprint | Medium | httpx with h2, header ordering |
| UA + header mismatch | High | UserAgentRotator with matching headers |
| Request timing patterns | High | Gaussian delay, token bucket |
| Datacenter IP | High | ProxyManager (residential proxies) |
| Cookie/session tracking | Medium | SessionManager per run |
| navigator.webdriver | N/A | No headless browser used |
