# Ethics and Technical Limits

> Where the personal and technical lines are, and how this design respects both.

---

## Terms of Service Analysis

### RemoteOK
- **API Status:** Documented public API at `https://remoteok.com/api`
- **ToS position:** RemoteOK explicitly provides a public API for job data access
- **robots.txt:** `/api` is not disallowed
- **Our usage:** Single request per scrape cycle with realistic delays — well within any reasonable rate limit
- **Verdict:** ✅ Fully permitted

### HN Who's Hiring (via Algolia)
- **API Status:** Algolia provides a public search API for HN content
- **ToS position:** HN content is publicly accessible. Algolia's search API for HN is widely used by developers
- **Rate limit:** 10,000 requests/day on the public tier — our usage (1 request/hour) is <0.3% of that
- **Verdict:** ✅ Fully permitted

### Indeed RSS
- **API Status:** Public RSS feed, no authentication required, indexed by Google
- **robots.txt:** RSS endpoints are not disallowed for crawlers
- **ToS nuance:** Indeed's general ToS prohibits "scraping" but their own RSS feed is designed for automated consumption. This is a grey area.
- **Our mitigation:** Realistic delays, one request per hour, standard RSS User-Agent headers. We treat it exactly as a feed reader would.
- **Verdict:** ⚠️ Likely permitted for RSS consumption; would need legal review before commercial use

### LinkedIn / Glassdoor / Naukri
- **ToS position:** Explicitly prohibit automated data collection
- **Technical barriers:** Auth walls, Datadome/Cloudflare Bot Management, CAPTCHA
- **Our decision:** Not implemented in the live demo. These platforms are used only as design examples in `DETECTION_SURFACE.md`.
- **Verdict:** ❌ Not included in live demo

---

## Technical Lines

### What this system will do:
- Fetch data from public endpoints (RSS, public JSON APIs) with no authentication
- Respect rate limits and never burst beyond 1 request/2 seconds per domain
- Store only job listing metadata (title, company, URL, description) — no PII
- Make no more requests than a human power user would browsing the same sites

### What this system will not do:
- Authenticate as a user to access auth-walled content
- Bypass CAPTCHAs
- Store user profile data, emails, or personal information
- Circumvent access controls (IP bans, account suspensions)
- Operate at scale that impacts server performance (no parallel bulk requests to a single domain)

---

## Personal Lines

1. **No auth-walled content.** If a site requires login to see job listings, that content stays off-limits. The value proposition of requiring login is often the data itself — respecting that boundary is non-negotiable.

2. **Rate respect over data completeness.** If a source starts rate-limiting, we back off. Getting fewer jobs is better than being a bad actor on someone else's infrastructure.

3. **No PII collection.** Job listings don't inherently contain PII. If a scraper ever encounters user-generated content with email addresses or phone numbers, those fields are not stored.

4. **Transparency in design.** The DECISIONS.md and this document exist so that anyone reviewing this codebase understands exactly what data is collected, from where, and why — with no hidden behaviour.

5. **Scope guardrail (from assessment).** The live demo runs against the low-risk sources listed above — not live LinkedIn or any authentication-required endpoint. This is both the right technical choice (stability) and the right ethical choice (ToS compliance).

---

## Data Retention

In production, stale job listings should be purged:
- `JobRepository.delete_old_jobs(before=datetime)` is implemented
- Recommended: delete jobs older than 90 days
- Rationale: We hold data only as long as it's useful; indefinite retention of scraped content raises unnecessary legal exposure

---

## If Deployed Commercially

Before deploying this system at commercial scale:
1. Obtain explicit API agreements with job platforms where available
2. Legal review of ToS for each source in the target jurisdiction
3. Implement data processing agreements if storing any user-generated content
4. Add rate-limit monitoring with automatic shutdown if a source pushes back
5. Consider using licensed data providers (Adzuna, Indeed API partner programme) instead of scraping
