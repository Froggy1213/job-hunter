"""Dummy scraper -- returns mock job listings for testing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import HttpUrl
from playwright.async_api import Page

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper


class DummyScraper(BaseScraper):

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.DUMMY

    async def fetch_jobs(self) -> list[JobPosting]:
        await asyncio.sleep(0.5)
        return [
            JobPosting(
                title="UX/UI Designer (B2B SaaS)",
                company="TechForward株式会社",
                url=HttpUrl("https://example.com/jobs/ux-ui-designer"),
                location="Tokyo, Minato-ku",
                source_platform=SourcePlatform.DUMMY,
                description="Lead redesign of enterprise analytics dashboard.",
                salary="¥7M – ¥10M",
                posted_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
            ),
            JobPosting(
                title="Graphic Designer (Marketing)",
                company="CreativeEdge Inc.",
                url=HttpUrl("https://example.com/jobs/graphic-designer-marketing"),
                location="Tokyo, Shibuya-ku",
                source_platform=SourcePlatform.DUMMY,
                description="Create visual assets for digital and print campaigns.",
                salary="¥4.5M – ¥6M",
                posted_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
            ),
            JobPosting(
                title="Product Designer",
                company="PixelPerfect Studio",
                url=HttpUrl("https://example.com/jobs/product-designer"),
                location="Tokyo, Meguro-ku (Remote OK)",
                source_platform=SourcePlatform.DUMMY,
                description="End-to-end product design for consumer mobile app.",
                salary=None,
                posted_at=None,
            ),
        ]

    async def parse_page(self, page: Page) -> list[JobPosting]:
        raise NotImplementedError("DummyScraper does not use Playwright.")
