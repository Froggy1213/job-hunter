"""Canonical domain model for a job listing.

``JobPosting`` is the single representation used across every layer:
- Scrapers produce instances of this model.
- The repository persists instances of this model.
- Bot handlers format instances of this model for display.

It is frozen (immutable) after construction.  This prevents accidental
state changes as job data flows between services, and makes the data
flow easier to reason about.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, HttpUrl

from models.enums import SourcePlatform


class JobPosting(BaseModel):
    """A single job listing scraped from a job board.

    Once constructed, instances are immutable (``frozen=True``) so callers
    cannot accidentally modify fields as data passes through the pipeline.
    """

    model_config = {"frozen": True}

    title: str = Field(
        min_length=1,
        max_length=512,
        description="Job title as listed on the source platform.",
    )
    company: str = Field(
        min_length=1,
        max_length=256,
        description="Company name as listed on the source platform.",
    )
    url: HttpUrl = Field(
        description="Direct URL to the job listing. Used as the deduplication key.",
    )
    location: str = Field(
        min_length=1,
        max_length=256,
        description="Work location (e.g. 'Tokyo, Minato-ku').",
    )
    source_platform: SourcePlatform = Field(
        description="The job board this listing was scraped from.",
    )
    description: str | None = Field(
        default=None,
        max_length=10_000,
        description="Full or truncated job description. May be missing for API-only scrapes.",
    )
    salary: str | None = Field(
        default=None,
        max_length=256,
        description="Salary range or amount as a human-readable string.",
    )
    posted_at: datetime | None = Field(
        default=None,
        description="When the job was posted on the source platform (if available).",
    )
    scraped_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this listing was scraped. Always UTC.",
    )
