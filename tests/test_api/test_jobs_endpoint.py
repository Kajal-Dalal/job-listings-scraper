"""
Tests for the /api/v1/jobs endpoints.

Validates:
- Pagination (page, page_size, total_pages)
- Filtering by source, keyword, remote, salary
- Single job detail endpoint
- 404 for unknown job ID
- API key authentication on protected endpoints
"""
import hashlib
import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient

from src.main import create_app
from src.storage.database import init_database
from src.storage.models import JobListing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    title="Software Engineer",
    company="TestCorp",
    source="test",
    remote=False,
    salary_min=None,
    salary_max=None,
    location="San Francisco, CA",
    tags=None,
    url_suffix="1",
) -> JobListing:
    url = f"https://example.com/jobs/{url_suffix}"
    key = f"{url.lower()}|{title.lower()}|{company.lower()}"
    return JobListing(
        id=str(uuid.uuid4()),
        source=source,
        title=title,
        company=company,
        location=location,
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency="USD",
        url=url,
        tags=tags or [],
        hash=hashlib.sha256(key.encode()).hexdigest(),
        scraped_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def db_with_jobs():
    """In-memory DB pre-populated with sample jobs."""
    db = init_database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()

    async with db.session() as session:
        jobs = [
            _make_job("Python Engineer", "RemoteFirst", source="remoteok", remote=True,
                      salary_min=80000, salary_max=120000, location="Remote", url_suffix="py1"),
            _make_job("React Developer", "WebAgency", source="hn_jobs", remote=False,
                      salary_min=70000, salary_max=100000, location="New York, NY", url_suffix="re1"),
            _make_job("DevOps Engineer", "CloudCorp", source="remoteok", remote=True,
                      salary_min=100000, salary_max=150000, location="Remote", url_suffix="de1"),
            _make_job("Junior Developer", "StartupXYZ", source="indeed_rss", remote=False,
                      salary_min=50000, salary_max=70000, location="Austin, TX", url_suffix="jr1"),
            _make_job("Data Scientist", "DataCo", source="hn_jobs", remote=True,
                      salary_min=110000, salary_max=160000, location="Remote", url_suffix="ds1"),
        ]
        for job in jobs:
            session.add(job)

    yield db
    await db.drop_tables()
    await db.close()


@pytest_asyncio.fixture(scope="function")
async def api_client(db_with_jobs):
    """AsyncClient for the FastAPI app using the pre-seeded test DB."""
    import os
    os.environ["API_KEY"] = "test-key-12345"
    os.environ["ENABLE_SCHEDULER"] = "false"

    app = create_app()
    app.state.db = db_with_jobs
    app.state.scheduler = None
    app.state.pipeline = None

    # Override database dependency
    from src.storage.database import get_db_session

    async def override_db():
        async with db_with_jobs.session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# GET /api/v1/jobs tests
# ---------------------------------------------------------------------------

class TestListJobs:
    """Tests for the paginated job list endpoint."""

    @pytest.mark.asyncio
    async def test_returns_200_and_job_list(self, api_client):
        """Basic list should return 200 with items."""
        resp = await api_client.get("/api/v1/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 5
        assert len(data["items"]) >= 1

    @pytest.mark.asyncio
    async def test_pagination_page_size(self, api_client):
        """page_size should limit results per page."""
        resp = await api_client.get("/api/v1/jobs?page=1&page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["page_size"] == 2
        assert data["has_next"] is True

    @pytest.mark.asyncio
    async def test_pagination_page_2(self, api_client):
        """Page 2 should return different items than page 1."""
        resp1 = await api_client.get("/api/v1/jobs?page=1&page_size=2")
        resp2 = await api_client.get("/api/v1/jobs?page=2&page_size=2")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        ids1 = {item["id"] for item in resp1.json()["items"]}
        ids2 = {item["id"] for item in resp2.json()["items"]}
        assert ids1.isdisjoint(ids2), "Page 1 and 2 should have different items"

    @pytest.mark.asyncio
    async def test_filter_by_source(self, api_client):
        """source filter should restrict results to that source."""
        resp = await api_client.get("/api/v1/jobs?source=remoteok")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["source"] == "remoteok"

    @pytest.mark.asyncio
    async def test_filter_remote_only(self, api_client):
        """remote_only=true should return only remote jobs."""
        resp = await api_client.get("/api/v1/jobs?remote_only=true")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["remote"] is True

    @pytest.mark.asyncio
    async def test_filter_salary_min(self, api_client):
        """salary_min should exclude jobs below the threshold."""
        resp = await api_client.get("/api/v1/jobs?salary_min=100000")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            has_qualifying_salary = (
                (item.get("salary_min") or 0) >= 100000
                or (item.get("salary_max") or 0) >= 100000
            )
            assert has_qualifying_salary

    @pytest.mark.asyncio
    async def test_filter_keyword(self, api_client):
        """keyword filter should match title/company/description."""
        resp = await api_client.get("/api/v1/jobs?keyword=Python")
        assert resp.status_code == 200
        data = resp.json()
        # At least one job should contain "Python"
        if data["total"] > 0:
            matched = any("python" in item["title"].lower() for item in data["items"])
            assert matched

    @pytest.mark.asyncio
    async def test_invalid_page_size_returns_422(self, api_client):
        """page_size > 100 should return 422 Unprocessable Entity."""
        resp = await api_client.get("/api/v1/jobs?page_size=999")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_total_pages_calculation(self, api_client):
        """total_pages should equal ceil(total / page_size)."""
        resp = await api_client.get("/api/v1/jobs?page_size=2")
        assert resp.status_code == 200
        data = resp.json()
        import math
        expected_pages = max(1, math.ceil(data["total"] / data["page_size"]))
        assert data["total_pages"] == expected_pages


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id} tests
# ---------------------------------------------------------------------------

class TestGetJobDetail:
    """Tests for the single job detail endpoint."""

    @pytest.mark.asyncio
    async def test_returns_job_by_id(self, api_client):
        """Should return a job when given a valid ID."""
        # Get an ID from the list first
        list_resp = await api_client.get("/api/v1/jobs?page_size=1")
        assert list_resp.status_code == 200
        job_id = list_resp.json()["items"][0]["id"]

        detail_resp = await api_client.get(f"/api/v1/jobs/{job_id}")
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        assert data["id"] == job_id

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_id(self, api_client):
        """Unknown job ID should return 404."""
        resp = await api_client.get("/api/v1/jobs/nonexistent-id-12345")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_detail_has_all_fields(self, api_client):
        """Detail response should include all required fields."""
        list_resp = await api_client.get("/api/v1/jobs?page_size=1")
        job_id = list_resp.json()["items"][0]["id"]

        detail_resp = await api_client.get(f"/api/v1/jobs/{job_id}")
        data = detail_resp.json()

        required_fields = ["id", "source", "title", "company", "url", "remote", "scraped_at"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

class TestAuthentication:
    """Tests for API key authentication on protected endpoints."""

    @pytest.mark.asyncio
    async def test_trigger_scrape_requires_api_key(self, api_client):
        """POST /scrape/trigger without API key should return 422 or 401."""
        resp = await api_client.post("/api/v1/scrape/trigger")
        assert resp.status_code in (401, 422)

    @pytest.mark.asyncio
    async def test_trigger_scrape_rejects_wrong_key(self, api_client):
        """POST /scrape/trigger with wrong API key should return 401."""
        resp = await api_client.post(
            "/api/v1/scrape/trigger",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_jobs_does_not_require_api_key(self, api_client):
        """GET /jobs should be publicly accessible (no auth needed)."""
        resp = await api_client.get("/api/v1/jobs")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, api_client):
        """Health endpoint should return 200 when DB is accessible."""
        resp = await api_client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_schema(self, api_client):
        """Health response should contain expected fields."""
        resp = await api_client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "db" in data
        assert data["db"] == "ok"
