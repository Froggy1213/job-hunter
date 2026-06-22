"""Tests for the ``JobPosting`` domain model."""

from datetime import datetime, timezone

import pytest
from pydantic import HttpUrl, ValidationError

from models.enums import SourcePlatform
from models.job_posting import JobPosting


def test_minimal_job_posting():
    """A JobPosting with only required fields should construct successfully."""
    job = JobPosting(
        title="Software Engineer",
        company="Test株式会社",
        url=HttpUrl("https://example.com/jobs/1"),
        location="Tokyo",
        source_platform=SourcePlatform.DUMMY,
    )
    assert job.title == "Software Engineer"
    assert job.company == "Test株式会社"
    assert str(job.url) == "https://example.com/jobs/1"
    assert job.location == "Tokyo"
    assert job.source_platform == SourcePlatform.DUMMY
    # Auto-filled fields
    assert job.description is None
    assert job.salary is None
    assert job.posted_at is None
    assert job.scraped_at is not None
    assert job.scraped_at.tzinfo == timezone.utc


def test_full_job_posting():
    """All fields should be accepted when provided."""
    posted = datetime(2026, 6, 1, tzinfo=timezone.utc)
    job = JobPosting(
        title="UX Designer",
        company="DesignCo",
        url=HttpUrl("https://jobs.example.com/ux"),
        location="Tokyo, Shibuya-ku",
        source_platform=SourcePlatform.DUMMY,
        description="Lead our design system.",
        salary="¥8M",
        posted_at=posted,
    )
    assert job.description == "Lead our design system."
    assert job.salary == "¥8M"
    assert job.posted_at == posted


def test_immutable_job_posting():
    """JobPosting is frozen -- direct attribute mutation should raise a
    ValidationError because frozen models reject ``__setattr__``."""
    job = JobPosting(
        title="SWE",
        company="Co",
        url=HttpUrl("https://example.com/j/2"),
        location="Osaka",
        source_platform=SourcePlatform.DUMMY,
    )
    # ``frozen=True`` prevents ``__setattr__`` on the model instance.
    with pytest.raises(ValidationError):
        job.title = "Changed"


def test_title_must_not_be_empty():
    """An empty title should fail validation."""
    with pytest.raises(ValidationError):
        JobPosting(
            title="",
            company="Co",
            url=HttpUrl("https://example.com/j/3"),
            location="Tokyo",
            source_platform=SourcePlatform.DUMMY,
        )


def test_invalid_url():
    """A non-URL string should fail validation."""
    with pytest.raises(ValidationError):
        JobPosting(
            title="SWE",
            company="Co",
            url="not-a-url",  # type: ignore[arg-type]
            location="Tokyo",
            source_platform=SourcePlatform.DUMMY,
        )


def test_serialization():
    """JobPosting should serialize to JSON-safe types when using ``mode='json'``.

    With ``mode='json'``, HttpUrl becomes a plain str and datetime becomes
    an ISO 8601 string -- suitable for JSON encoding.
    """
    job = JobPosting(
        title="Test",
        company="Co",
        url=HttpUrl("https://x.com/j"),
        location="Tokyo",
        source_platform=SourcePlatform.DUMMY,
    )
    data = job.model_dump(mode="json")
    assert isinstance(data["url"], str)
    assert data["url"] == "https://x.com/j"
    assert data["source_platform"] == "dummy"
    assert isinstance(data["scraped_at"], str)
