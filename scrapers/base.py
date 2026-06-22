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

    def __init__(self, headless: bool = True) -> None:
        """Initialise the scraper.Act as an Expert Python Developer. We are continuing to build our job aggregator bot. Now we need to implement a scraper for GaijinPot Jobs (jobs.gaijinpot.com), which is the top platform for foreigners seeking work in Japan (often offering visa support and English-friendly environments).

Please create a `GaijinPotScraper` class that inherits from our existing `BaseScraper`.

### Requirements:
1. **Target URL Strategy:** Construct the search URL targeting Tokyo and Design/Creative/IT keywords. (e.g., `https://jobs.gaijinpot.com/job/index/keywords/design/region/22` - where region 22 is usually Tokyo, or simply use keyword-based search URLs).
2. **Playwright Automation:**
   - Use async Playwright to navigate.
   - Wait for the job listing cards to appear. GaijinPot uses standard HTML tables or lists for job postings.
   - Handle pagination using URL parameters (e.g., `&page=X` or `/page/X`). Implement logic to scrape up to 2 pages maximum.
3. **Data Extraction:**
   - Extract the Job Title, Company Name, and the direct URL to the job posting.
   - GaijinPot URLs are usually absolute, but if they are relative, use `urllib.parse.urljoin`.
   - Map these to the `JobPosting` data model, setting `source_platform` to "GAIJINPOT".
4. **Integration:**
   - Add `GAIJINPOT` to the `SourcePlatform` enum (if not already present).
   - Save the code in `scrapers/implementations/gaijinpot.py`.
   - Update `main.py` to register this new scraper in the `ScraperOrchestrator`.

### Constraints:
- Include robust error handling (try/except blocks) per job card so a single malformed card doesn't break the whole loop.
- Use the same stealth settings (User-Agent, locale) for consistency.
- Output ONLY the necessary Python code and brief instructions for registration.

        Args:
            headless: Whether to launch the browser in headless mode.
                Set to ``False`` during development to see what Playwright
                is doing.
        """
        self._headless = headless

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
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                return await self.parse_page(page)
            except Exception as exc:
                raise ScraperError(
                    f"Playwright scrape failed for {url}: {exc}"
                ) from exc
            finally:
                await browser.close()
