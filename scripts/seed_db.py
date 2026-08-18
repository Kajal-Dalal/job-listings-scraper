"""
Seed the database with sample job listings for development/demo.

Usage:
    python scripts/seed_db.py
"""
import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.settings import get_settings
from src.monitoring.logger import configure_logging
from src.storage.database import init_database
from src.storage.models import JobListing


SAMPLE_JOBS = [
    {
        "title": "Senior Python Engineer",
        "company": "RemoteFirst Inc",
        "source": "remoteok",
        "location": "Worldwide",
        "remote": True,
        "salary_min": 120000,
        "salary_max": 160000,
        "url": "https://remoteok.com/remote-jobs/senior-python-engineer-1",
        "tags": ["python", "fastapi", "aws", "backend"],
        "description": "We are looking for a senior Python engineer to join our distributed team. You'll work on our data pipeline and API services.",
    },
    {
        "title": "React Frontend Developer",
        "company": "WebAgency NYC",
        "source": "hn_jobs",
        "location": "New York, NY",
        "remote": False,
        "salary_min": 90000,
        "salary_max": 130000,
        "url": "https://news.ycombinator.com/item?id=38000001",
        "tags": ["react", "typescript", "frontend"],
        "description": "Frontend role for an agency building products for Fortune 500 clients.",
    },
    {
        "title": "DevOps Engineer",
        "company": "CloudScale",
        "source": "remoteok",
        "location": "Remote",
        "remote": True,
        "salary_min": 130000,
        "salary_max": 180000,
        "url": "https://remoteok.com/remote-jobs/devops-engineer-2",
        "tags": ["kubernetes", "docker", "aws", "terraform", "devops"],
        "description": "Lead our infrastructure team. Experience with Kubernetes at scale required.",
    },
    {
        "title": "Data Scientist - ML Focus",
        "company": "DataCo Analytics",
        "source": "hn_jobs",
        "location": "San Francisco, CA",
        "remote": True,
        "salary_min": 140000,
        "salary_max": 200000,
        "url": "https://news.ycombinator.com/item?id=38000002",
        "tags": ["python", "machine learning", "pytorch", "ml", "data science"],
        "description": "Join our ML team building recommendation systems at scale. PhD preferred.",
    },
    {
        "title": "Backend Engineer (Go/Rust)",
        "company": "SpeedTech",
        "source": "indeed_rss",
        "location": "Austin, TX",
        "remote": True,
        "salary_min": 110000,
        "salary_max": 150000,
        "url": "https://www.indeed.com/viewjob?jk=abc123",
        "tags": ["golang", "rust", "backend", "distributed systems"],
        "description": "High-performance backend systems in Go and Rust. We care about latency.",
    },
    {
        "title": "Junior Frontend Developer",
        "company": "StartupXYZ",
        "source": "indeed_rss",
        "location": "Remote",
        "remote": True,
        "salary_min": 55000,
        "salary_max": 80000,
        "url": "https://www.indeed.com/viewjob?jk=def456",
        "tags": ["javascript", "react", "frontend"],
        "description": "Great opportunity for a junior developer to grow. Mentorship provided.",
    },
    {
        "title": "Full Stack Engineer",
        "company": "ProductCo",
        "source": "remoteok",
        "location": "Remote",
        "remote": True,
        "salary_min": 100000,
        "salary_max": 140000,
        "url": "https://remoteok.com/remote-jobs/fullstack-engineer-3",
        "tags": ["python", "react", "postgresql", "fullstack"],
        "description": "Build our SaaS product end-to-end. Django backend, React frontend.",
    },
    {
        "title": "iOS Engineer",
        "company": "MobileFirst",
        "source": "hn_jobs",
        "location": "Seattle, WA",
        "remote": False,
        "salary_min": 130000,
        "salary_max": 170000,
        "url": "https://news.ycombinator.com/item?id=38000003",
        "tags": ["swift", "ios", "mobile"],
        "description": "Senior iOS engineer to lead our mobile team building consumer apps.",
    },
]


def make_job_listing(data: dict, days_ago: int = 0) -> JobListing:
    url = data["url"]
    title = data["title"]
    company = data["company"]
    key = f"{url.lower()}|{title.lower()}|{company.lower()}"
    dedup_hash = hashlib.sha256(key.encode()).hexdigest()

    scraped_at = datetime.utcnow() - timedelta(days=days_ago)
    posted_at = scraped_at - timedelta(hours=6)

    return JobListing(
        id=str(uuid.uuid4()),
        source=data["source"],
        external_id=url,
        title=title,
        company=company,
        location=data.get("location"),
        remote=data.get("remote", False),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        salary_currency="USD",
        description=data.get("description"),
        url=url,
        tags=data.get("tags", []),
        hash=dedup_hash,
        scraped_at=scraped_at,
        posted_at=posted_at,
    )


async def seed():
    configure_logging(log_level="INFO", log_format="console")
    settings = get_settings()

    print(f"Seeding database: {settings.database_url}")
    db = init_database(settings.database_url)
    await db.create_tables()

    inserted = 0
    async with db.session() as session:
        from src.storage.repository import JobRepository
        repo = JobRepository(session)

        for i, job_data in enumerate(SAMPLE_JOBS):
            job = make_job_listing(job_data, days_ago=i % 7)
            existing = await repo.exists_by_hash(job.hash)
            if not existing:
                session.add(job)
                inserted += 1
                print(f"  ✓ Inserted: {job.title} @ {job.company}")
            else:
                print(f"  - Skipped (exists): {job.title} @ {job.company}")

    await db.close()
    print(f"\nDone. Inserted {inserted}/{len(SAMPLE_JOBS)} jobs.")


if __name__ == "__main__":
    asyncio.run(seed())
