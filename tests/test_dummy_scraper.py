"""Tests for the DummyScraper."""

import pytest

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.implementations.dummy_scraper import DummyScraper


@pytest.mark.asyncio
async def test_dummy_scraper_platform():
    """The dummy scraper should report DUMMY as its platform."""
    scraper = DummyScraper(headless=True)
    assert scraper.platform == SourcePlatform.DUMMY


@pytest.mark.asyncio
async def test_dummy_scraper_fetch_jobs():
    """fetch_jobs() should return exactly 3 JobPosting objects."""
    scraper = DummyScraper(headless=True)
    jobs = await scraper.fetch_jobs()

    assert len(jobs) == 3
    for job in jobs:
        assert isinstance(job, JobPosting)
        assert job.source_platform == SourcePlatform.DUMMY
        assert job.title
        assert job.company
        assert job.location


@pytest.mark.asyncio
async def test_dummy_scraper_parse_page_raises():
    """The dummy scraper should raise NotImplementedError for parse_page."""
    scraper = DummyScraper(headless=True)
    with pytest.raises(NotImplementedError):
        await scraper.parse_page(None)  # type: ignore[arg-type]
