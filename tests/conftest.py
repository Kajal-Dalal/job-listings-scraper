"""
Shared pytest fixtures.

Provides:
- async test session
- in-memory SQLite database
- pre-populated job fixtures
- FastAPI test client
"""
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import Settings
from src.main import create_app
from src.storage.database import Database, init_database
from src.storage.models import Base, JobListing, ScrapeRun


# ---- Event loop ----

@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---- In-memory database ----

@pytest_asyncio.fixture(scope="function")
async def test_db() -> AsyncGenerator[Database, None]:
    """
    Provide a fresh in-memory SQLite database for each test function.
    Tables are created and dropped automatically.
    """
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    yield db
    await db.drop_tables()
    await db.close()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_db: Database) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session backed by the test DB."""
    async with test_db.session() as session:
        yield session


# ---- Sample data factories ----

def make_job(
    title="Software Engineer",
    company="Test Corp",
    url="https://example.com/jobs/1",
    source="test",
    remote=True,
    salary_min=80000,
    salary_max=120000,
    **kwargs,
) -> JobListing:
    """Factory for JobListing ORM objects."""
    import hashlib
    import uuid

    key = f"{url.lower()}|{title.lower()}|{company.lower()}"
    hash_val = hashlib.sha256(key.encode()).hexdigest()

    return JobListing(
        id=str(uuid.uuid4()),
        source=source,
        title=title,
        company=company,
        url=url,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency="USD",
        hash=hash_val,
        tags=["python", "fastapi"],
        **kwargs,
    )


@pytest.fixture
def sample_job() -> JobListing:
    """A single sample job listing."""
    return make_job()


@pytest.fixture
def sample_jobs() -> list:
    """A list of 5 diverse sample job listings."""
    return [
        make_job(
            title=f"Engineer {i}",
            company=f"Company {i}",
            url=f"https://example.com/jobs/{i}",
            source="test",
        )
        for i in range(5)
    ]


# ---- FastAPI test client ----

@pytest.fixture(scope="function")
def test_app(test_db):
    """
    FastAPI app configured for testing.
    Uses the test DB and disables the scheduler.
    """
    import os
    os.environ["ENABLE_SCHEDULER"] = "false"
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    os.environ["API_KEY"] = "test-api-key"
    os.environ["LOG_FORMAT"] = "console"

    app = create_app()
    # Override the DB with our test DB
    app.state.db = test_db
    app.state.scheduler = None
    app.state.pipeline = None
    return app


@pytest.fixture(scope="function")
def client(test_app) -> TestClient:
    """Synchronous test client."""
    with TestClient(test_app) as c:
        yield c


# ---- RSS fixtures ----

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Job Feed</title>
    <link>https://example.com/jobs</link>
    <description>Test RSS job feed</description>
    <item>
      <title>Senior Python Engineer at TechCorp</title>
      <link>https://example.com/jobs/python-engineer</link>
      <description>We are looking for a senior Python engineer with 5+ years experience.
        Remote OK. Salary: $120,000 - $160,000/year.</description>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <guid>https://example.com/jobs/python-engineer</guid>
    </item>
    <item>
      <title>Frontend React Developer at StartupXYZ</title>
      <link>https://example.com/jobs/react-dev</link>
      <description>Looking for a React developer. Remote position. $80k-$110k.</description>
      <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
      <guid>https://example.com/jobs/react-dev</guid>
    </item>
  </channel>
</rss>"""


REMOTEOK_SAMPLE = [
    {"legal": "RemoteOK API"},  # First element is legal notice
    {
        "id": 12345,
        "position": "Senior Python Developer",
        "company": "RemoteFirst Inc",
        "url": "/remote-jobs/python-developer",
        "tags": ["python", "django", "aws"],
        "description": "<p>We are looking for a Python developer.</p>",
        "date": "2024-01-15T10:00:00Z",
        "salary_min": 90000,
        "salary_max": 140000,
        "location": "Worldwide",
    },
    {
        "id": 12346,
        "position": "React Frontend Engineer",
        "company": "WebCo",
        "url": "/remote-jobs/react-frontend",
        "tags": ["react", "typescript", "frontend"],
        "description": "<p>Frontend role for React experts.</p>",
        "date": "2024-01-14T10:00:00Z",
        "salary_min": 70000,
        "salary_max": 110000,
        "location": "Worldwide",
    },
]
