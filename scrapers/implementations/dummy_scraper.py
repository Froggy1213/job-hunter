"""Dummy scraper -- returns mock job listings for testing.

This scraper demonstrates the ``BaseScraper`` contract without
requiring a real website or API.  It returns three hard-coded
``JobPosting`` objects after a simulated network delay.

Use this as a reference when implementing real scrapers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from pydantic import HttpUrl
from playwright.async_api import Page

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper


class DummyScraper(BaseScraper):
    """A scraper that returns mock data to validate the pipeline.

    Architecture note: this scraper overrides ``fetch_jobs()`` directly
    because it doesn't need Playwright.  Real scrapers that target
    JS-rendered boards should instead override ``parse_page()`` and call
    ``self._scrape_with_playwright(url)`` from ``fetch_jobs()``.
    """

    _MOCK_JOBS: list[dict] = [
        {
            "title": "UX/UI Designer (B2B SaaS)",
            "company": "TechForward株式会社",
            "url": "https://example.com/jobs/ux-ui-designer",
            "location": "Tokyo, Minato-ku",
            "description": (
                "Lead the redesign of our enterprise analytics dashboard. "
                "5+ years of UX/UI design experience required. "
                "Figma expertise is a must."
            ),
            "salary": "¥7M – ¥10M",
            "posted_at": datetime(2026, 6, 15, tzinfo=timezone.utc),
        },
        {
            "title": "Graphic Designer (Marketing)",
            "company": "CreativeEdge Inc.",
            "url": "https://example.com/jobs/graphic-designer-marketing",
            "location": "Tokyo, Shibuya-ku",
            "description": (
                "Create visual assets for digital and print marketing campaigns. "
                "Adobe Creative Suite proficiency required."
            ),
            "salary": "¥4.5M – ¥6M",
            "posted_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
        },
        {
            "title": "Product Designer",
            "company": "PixelPerfect Studio",
            "url": "https://example.com/jobs/product-designer",
            "location": "Tokyo, Meguro-ku (Remote OK)",
            "description": (
                "End-to-end product design for a consumer mobile app. "
                "User research, wireframing, prototyping, and visual design."
            ),
            "salary": None,
            "posted_at": None,
        },
    ]

    @property
    def platform(self) -> SourcePlatform:
        """Return the dummy platform identifier."""
        return SourcePlatform.DUMMY

    async def fetch_jobs(self) -> list[JobPosting]:
        """Return three mock design-job listings after a simulated delay.

        The ``asyncio.sleep(0.5)`` mimics network latency so the
        orchestrator's timing behaviour is observable during testing.
        """
        await asyncio.sleep(0.5)  # Simulate network latency

        return [
            JobPosting(
                title=data["title"],
                company=data["company"],
                url=HttpUrl(data["url"]),
                location=data["location"],
                source_platform=self.platform,
                description=data.get("description"),
                salary=data.get("salary"),
                posted_at=data.get("posted_at"),
            )
            for data in self._MOCK_JOBS
        ]

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Not used -- the dummy scraper doesn't use Playwright.

        Raises:
            NotImplementedError: Always, since this scraper works from
                in-memory data.
        """
        raise NotImplementedError(
            "DummyScraper does not use Playwright.  Override fetch_jobs() instead."
        )
