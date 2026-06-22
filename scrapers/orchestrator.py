"""Scraper orchestrator -- runs all registered scrapers concurrently.

The orchestrator is the entry point for scraping operations.  It:
1. Runs every registered scraper in parallel via ``asyncio.gather``.
2. Catches per-scraper exceptions so one failure doesn't kill others.
3. Deduplicates results by URL before persisting.
4. Returns a count of *new* jobs per platform.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from database.repository import JobRepository
from models.enums import SourcePlatform
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.orchestrator")


class ScraperOrchestrator:
    """Coordinates concurrent scraping across multiple job boards.

    Args:
        scrapers: The list of ``BaseScraper`` strategies to execute.
        repository: The ``JobRepository`` used for deduplication checks
            and persistence.
    """

    def __init__(
        self,
        scrapers: Sequence[BaseScraper],
        repository: JobRepository,
    ) -> None:
        self._scrapers = list(scrapers)
        self._repository = repository

    @property
    def platforms(self) -> list[SourcePlatform]:
        """Return the platforms covered by registered scrapers."""
        return [s.platform for s in self._scrapers]

    async def run_all(self) -> dict[SourcePlatform, int]:
        """Execute all scrapers in parallel and persist new listings.

        Each scraper runs in its own task.  Exceptions from individual
        scrapers are caught, logged, and do NOT propagate -- a failing
        scraper never blocks the others.

        Deduplication: a job posting is only persisted if its URL does
        not already exist in the repository.  This is checked per-item
        so scrapers can be re-run safely at any interval.

        Returns:
            A dictionary mapping each ``SourcePlatform`` to the number
            of **new** jobs persisted during this run.  Failed scrapers
            always contribute ``0``.
        """
        logger.info(
            "Starting scrape run",
            extra={"platforms": [p.value for p in self.platforms]},
        )

        results = await asyncio.gather(
            *(self._run_one(s) for s in self._scrapers),
            return_exceptions=True,
        )

        counts: dict[SourcePlatform, int] = {}
        for scraper, result in zip(self._scrapers, results):
            if isinstance(result, Exception):
                logger.error(
                    "Scraper failed",
                    extra={
                        "platform": scraper.platform.value,
                        "error": str(result),
                    },
                )
                counts[scraper.platform] = 0
            else:
                counts[scraper.platform] = result

        total = sum(counts.values())
        logger.info("Scrape run complete", extra={"new_jobs": total, "by_platform": counts})
        return counts

    async def _run_one(self, scraper: BaseScraper) -> int:
        """Execute a single scraper, deduplicate, and persist.

        Returns the count of *new* jobs saved for this scraper.
        """
        jobs = await scraper.fetch_jobs()
        logger.debug(
            "Scraper returned",
            extra={"platform": scraper.platform.value, "count": len(jobs)},
        )

        new_count = 0
        for job in jobs:
            url_str = str(job.url)
            if await self._repository.exists(url_str):
                logger.debug("Skipping duplicate", extra={"url": url_str})
                continue

            await self._repository.save(job)
            new_count += 1

        logger.info(
            "Scraper finished",
            extra={
                "platform": scraper.platform.value,
                "total": len(jobs),
                "new": new_count,
            },
        )
        return new_count
