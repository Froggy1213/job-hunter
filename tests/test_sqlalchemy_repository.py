"""Integration tests for SQLAlchemyJobRepository.

These tests use an in-memory SQLite database, so they exercise the
real SQLAlchemy path without requiring an external database server.
"""

from datetime import datetime, timezone

import pytest
from pydantic import HttpUrl

from models.enums import SourcePlatform
from models.job_posting import JobPosting


def _make_job(title: str = "Software Engineer", url: str = "https://example.com/jobs/1") -> JobPosting:
    """Factory helper for creating test JobPosting instances."""
    return JobPosting(
        title=title,
        company="Test株式会社",
        url=HttpUrl(url),
        location="Tokyo",
        source_platform=SourcePlatform.DUMMY,
    )


@pytest.mark.asyncio
async def test_save_and_exists(repository):
    """After saving, exists() should return True for that URL."""
    job = _make_job(url="https://example.com/jobs/save-test")
    assert not await repository.exists("https://example.com/jobs/save-test")

    saved = await repository.save(job)
    assert saved.title == job.title
    assert saved.scraped_at is not None

    assert await repository.exists("https://example.com/jobs/save-test")


@pytest.mark.asyncio
async def test_get_all_returns_newest_first(repository):
    """get_all() should return jobs ordered by scraped_at descending."""
    job1 = _make_job(title="First", url="https://example.com/jobs/1")
    job2 = _make_job(title="Second", url="https://example.com/jobs/2")

    await repository.save(job1)
    await repository.save(job2)

    jobs = await repository.get_all()
    assert len(jobs) == 2
    # Second saved should appear first (newer scraped_at)
    assert jobs[0].title == "Second"
    assert jobs[1].title == "First"


@pytest.mark.asyncio
async def test_get_by_source_filter(repository):
    """get_by_source() should only return jobs for the given platform."""
    job = _make_job(url="https://example.com/jobs/src-filter")
    await repository.save(job)

    # Should find it for DUMMY
    jobs = await repository.get_by_source(SourcePlatform.DUMMY)
    assert len(jobs) == 1
    assert jobs[0].source_platform == SourcePlatform.DUMMY


@pytest.mark.asyncio
async def test_save_duplicate_url_raises(repository):
    """Saving two jobs with the same URL should raise RepositoryError."""
    job1 = _make_job(url="https://example.com/jobs/dup")
    await repository.save(job1)

    job2 = _make_job(title="Different title", url="https://example.com/jobs/dup")
    from core.exceptions import RepositoryError

    with pytest.raises(RepositoryError):
        await repository.save(job2)


@pytest.mark.asyncio
async def test_exists_returns_false_for_unknown_url(repository):
    """exists() should return False for URLs not in the database."""
    assert not await repository.exists("https://nonexistent.example.com/job")


@pytest.mark.asyncio
async def test_get_all_empty(repository):
    """get_all() should return an empty list when no jobs are stored."""
    jobs = await repository.get_all()
    assert jobs == []
