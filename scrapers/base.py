"""Abstract base class for job board scrapers (Strategy pattern).

Every concrete scraper is a strategy that knows how to fetch job
listings from one specific job board.  The orchestrator treats all
scrapers polymorphically -- it calls ``fetch_jobs()`` on each one
without knowing which site it targets.

Two implementation paths are supported:

1. **API-first boards**: Override ``fetch_jobs()`` directly and make
   an HTTP call (e.g. with ``httpx``).  ``parse_page()`` can raise
   ``NotImplementedError``.

2. **JS-rendered boards**: Override ``parse_page(page)`` with
   board-specific CSS/XPath selectors, then call
   ``self._scrape_with_playwright(url)`` from ``fetch_jobs()``.
   The base class handles browser lifecycle, locale, and timeouts.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from playwright.async_api import Browser, Page, async_playwright

from core.exceptions import ScraperError
from models.enums import SourcePlatform
from models.job_posting import JobPosting

logger = logging.getLogger("job_hunter.scraper")


class BaseScraper(ABC):
    """Abstract strategy for scraping a single job board.

    Subclasses MUST:
        - Implement the ``platform`` property
        - Implement ``fetch_jobs()`` (or ``parse_page()`` + use the helper)

    Subclasses MAY:
        - Call ``self._scrape_with_playwright(url)`` to reuse browser setup
        - Override ``self._user_agent`` to spoof a different UA string
    """

    # Default user agent -- a recent Chrome on macOS.
    # Subclasses can override this class attribute.
    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000) -> None:
        """Initialise the scraper.

        Args:
            headless: Whether to launch the browser in headless mode.
                Set to ``False`` during development to see what Playwright
                is doing.
            timeout_ms: Page navigation timeout in milliseconds.
        """
        self._headless = headless
        self._timeout_ms = timeout_ms

    # ------------------------------------------------------------------
    # Job title filter (shared across all scrapers)
    # ------------------------------------------------------------------

    # Titles containing any of these words are REJECTED.
    _STOP_WORDS: set[str] = {
        "cad", "mechanical", "machine", "architect", "fashion",
        "game", "3d", "cg", "video", "movie",
        "機械", "建築", "アパレル", "ゲーム", "映像", "施工",
    }

    # Titles MUST contain at least one of these words to be ACCEPTED.
    _TARGET_WORDS: set[str] = {
        "web", "ui", "ux", "graphic", "designer",
        "デザイン", "デザイナー", "フロントエンド",
    }

    @staticmethod
    def is_target_job(title: str) -> bool:
        """Return ``True`` if *title* is a target design job.

        Filtering logic (case-insensitive):

        1. If *title* contains any stop-word → reject immediately.
        2. If *title* contains any target word → accept.
        3. Otherwise → reject.
        """
        lower = title.lower()

        for stop in BaseScraper._STOP_WORDS:
            if stop in lower:
                return False

        for target in BaseScraper._TARGET_WORDS:
            if target in lower:
                return True

        return False

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def platform(self) -> SourcePlatform:
        """Return the ``SourcePlatform`` enum value for this scraper.

        This property is read by the orchestrator for logging and
        attribution.  Each concrete scraper returns its own enum member.
        """
        ...

    @abstractmethod
    async def fetch_jobs(self) -> list[JobPosting]:
        """Fetch and parse all available job listings.

        This is the main entry point called by the orchestrator.
        The returned list may be empty if no new listings are found,
        but an empty list is distinct from a scraper failure (which
        raises ``ScraperError``).

        Returns:
            A list of validated ``JobPosting`` domain models, each with
            the scraper's ``platform`` already set.

        Raises:
            ScraperError: If the fetch or parse operation fails.
        """
        ...

    @abstractmethod
    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Parse a fully-loaded Playwright ``Page`` and extract job listings.

        Called by ``_scrape_with_playwright`` after navigation completes.
        Subclasses implement board-specific CSS/XPath selectors here.

        Args:
            page: A Playwright ``Page`` that has already navigated to the
                target URL and waited for ``networkidle``.

        Returns:
            A list of ``JobPosting`` domain models extracted from the page.
        """
        ...

    # ------------------------------------------------------------------
    # Shared Playwright helper
    # ------------------------------------------------------------------

    async def _scrape_with_playwright(self, url: str) -> list[JobPosting]:
        """Open *url* in a headless Chromium browser and delegate to
        ``parse_page()``.

        This helper encapsulates the Playwright lifecycle (launch →
        create context → navigate → parse → close) so subclasses don't
        need to repeat browser setup logic.

        The browser context is configured with Japanese locale and
        Tokyo timezone so date strings render correctly for
        ``parse_page()`` selectors.

        Args:
            url: The job board URL to navigate to.

        Returns:
            The result of ``self.parse_page(page)``.

        Raises:
            ScraperError: Wraps any Playwright or parsing exception.
        """
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=self._user_agent,
                )
                page = await context.new_page()
                logger.info("Navigating", extra={"url": url})
                await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
                return await self.parse_page(page)
            except Exception as exc:
                raise ScraperError(
                    f"Playwright scrape failed for {url}: {exc}"
                ) from exc
            finally:
                await browser.close()
