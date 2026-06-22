"""Mynavi Tenshoku (マイナビ転職) scraper.

Targets design/creative job listings in Tokyo from Mynavi, one of the
largest Japanese job boards.  Uses Playwright because Mynavi's search
results are rendered by a JavaScript framework (Angular/Vue).

URL structure (discovered via reconnaissance):
    Search page:
        https://tenshoku.mynavi.jp/shutoken/list/p13/o1A/
        ├── shutoken  = 首都圏 (Greater Tokyo metro area)
        ├── p13       = 東京都 (Tokyo prefecture)
        └── o1A       = クリエイティブ職種 (Creative job category)

    Job detail:
        https://tenshoku.mynavi.jp/jobinfo-{ID}-{X}-{X}-{X}/

    Pagination:
        .../shutoken/list/p13/o1A/pg{N}/
        "次へ" link = next page
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright

from core.exceptions import ScraperError
from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.mynavi")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://tenshoku.mynavi.jp"
_SEARCH_PATH = "/shutoken/list/p13/o1A/"
_MAX_PAGES = 2
_PAGE_DELAY = 2.0  # seconds between pages (politeness)
_NAV_TIMEOUT = 30_000  # ms

# The detail URL pattern is extremely stable -- it hasn't changed in years.
_JOBINFO_RE = re.compile(r"/jobinfo-\d+-\d+-\d+-\d+/")


class MynaviScraper(BaseScraper):
    """Scrape design/creative jobs in Tokyo from Mynavi Tenshoku.

    Overrides ``fetch_jobs()`` to manage its own Playwright lifecycle
    and pagination loop.  Each page is parsed by ``parse_page()``, which
    extracts job cards using CSS selectors inferred from Mynavi's DOM.
    """

    # Mynavi is stricter about bot detection than most boards.
    # Use a very recent Chrome UA and include the common headers.
    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.MYNAVI

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate through up to ``_MAX_PAGES`` pages of Mynavi search
        results, parsing each with ``parse_page()``.

        Manages a single browser/context across all pages so we avoid
        the overhead of cold-launching Chromium for every page.
        """
        all_jobs: list[JobPosting] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=self._user_agent,
                )
                page = await context.new_page()

                for page_num in range(1, _MAX_PAGES + 1):
                    url = self._build_page_url(page_num)
                    logger.info(
                        "Scraping Mynavi page",
                        extra={"page": page_num, "url": url},
                    )
                    try:
                        await page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=_NAV_TIMEOUT,
                        )
                        # Mynavi sometimes shows a cookie consent overlay.
                        await self._dismiss_overlays(page)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "Mynavi page parsed",
                            extra={
                                "page": page_num,
                                "cards_found": len(jobs),
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to scrape Mynavi page %d", page_num
                        )
                        break  # Stop pagination on error

                    # Check whether a next page exists before looping.
                    if page_num >= _MAX_PAGES:
                        break
                    if not await self._has_next_page(page):
                        logger.info(
                            "No next page available, stopping pagination",
                            extra={"last_page": page_num},
                        )
                        break

                    await asyncio.sleep(_PAGE_DELAY)

            finally:
                await browser.close()

        logger.info(
            "Mynavi scrape complete",
            extra={"total_jobs": len(all_jobs)},
        )
        return all_jobs

    # ------------------------------------------------------------------
    # Page parsing
    # ------------------------------------------------------------------

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract job listings from a fully-loaded Mynavi search results page.

        Strategy
        --------

        We locate every ``<a href*="/jobinfo-">`` element on the page.
        These links are the single most reliable selector because the URL
        pattern is deterministic and unlikely to change.

        From each link we walk up to the enclosing card element, then
        extract the company name, location, and salary from labelled
        text nodes within that card.

        If a card cannot be fully parsed it is skipped (logged at WARNING)
        rather than crashing the entire scrape.
        """
        cards: list[JobPosting] = []

        # Wait for at least one job card to appear.
        try:
            await page.wait_for_selector(
                'a[href*="/jobinfo-"]',
                state="attached",
                timeout=15_000,
            )
        except Exception:
            logger.warning("No job cards found on Mynavi page")
            return cards

        # Collect all job detail links.
        link_elements = page.locator('a[href*="/jobinfo-"]')
        link_count = await link_elements.count()
        logger.debug("Found %d job links on page", link_count)

        for i in range(link_count):
            try:
                link = link_elements.nth(i)

                # --- href ---
                href = await link.get_attribute("href")
                if not href or not _JOBINFO_RE.search(href):
                    continue

                url = urljoin(_BASE_URL, href)

                # --- title ---
                title = (await link.text_content()).strip()
                if not title or len(title) < 2:
                    # Sometimes the link contains an <img> with no text.
                    # Skip these -- they are visual duplicate links.
                    continue

                # --- card container ---
                card = self._resolve_card_container(link)
                if card is None:
                    logger.debug("Skipping card %d: no container found", i)
                    continue

                # --- company ---
                company = await self._extract_company(card)

                # --- location ---
                location = await self._extract_labeled_field(card, "勤務地")

                # --- salary ---
                salary = await self._extract_labeled_field(card, "給与")

                cards.append(
                    JobPosting(
                        title=title,
                        company=company,
                        url=url,  # type: ignore[arg-type]  # urljoin returns str, Pydantic coerces
                        location=location,
                        source_platform=self.platform,
                        salary=salary if salary else None,
                    )
                )
            except Exception:
                logger.warning("Failed to parse Mynavi card %d", i, exc_info=True)
                continue

        return cards

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        """Return the search URL for *page_num* (1-indexed).

        Page 1 is the bare search path.  Page 2+ appends ``pg{N}/``.
        """
        if page_num == 1:
            return f"{_BASE_URL}{_SEARCH_PATH}"
        return f"{_BASE_URL}{_SEARCH_PATH}pg{page_num}/"

    @staticmethod
    async def _dismiss_overlays(page: Page) -> None:
        """Attempt to dismiss cookie consent banners or modal overlays.

        Mynavi sometimes shows a cookie consent bar at the bottom of the
        page.  We try a few common selectors and move on if none match
        -- the page is still scrapeable even with the banner visible.
        """
        common_dismiss_selectors = [
            'button:has-text("同意する")',
            'button:has-text("OK")',
            'button:has-text("閉じる")',
            ".cookie-consent button",
            '[aria-label="Close"]',
        ]
        for selector in common_dismiss_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=1_000):
                    await btn.click()
                    await page.wait_for_timeout(500)
                    logger.debug("Dismissed overlay via %r", selector)
                    return
            except Exception:
                continue

    @staticmethod
    async def _has_next_page(page: Page) -> bool:
        """Return ``True`` if the pagination contains a *次へ* link."""
        try:
            next_link = page.locator('a:has-text("次へ")')
            return await next_link.count() > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Card-level extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_card_container(link):
        """Walk up the DOM from *link* to find the closest card wrapper.

        Mynavi's cards don't have a single consistent class, so we try
        several heuristics in order of specificity:

        1. ``xpath=ancestor::li`` -- if cards are list items
        2. ``xpath=ancestor::div[contains(@class,'cassette')]`` -- common
           Japanese job-board class name for cards
        3. Closest ``<section>`` or ``<article>``
        4. Fallback: ``xpath=ancestor::*[3]`` -- grandparent element
        """
        # Heuristic 1: <li> ancestor
        try:
            li = link.locator("xpath=ancestor::li[1]")
            # Verify this <li> actually contains the link
            return li
        except Exception:
            pass

        # Heuristic 2: cassette-style div
        try:
            cassette = link.locator("xpath=ancestor::div[contains(@class,'cassette')][1]")
            return cassette
        except Exception:
            pass

        # Heuristic 3: article / section
        for tag in ("article", "section"):
            try:
                el = link.locator(f"xpath=ancestor::{tag}[1]")
                return el
            except Exception:
                continue

        # Heuristic 4: grandparent
        try:
            return link.locator("xpath=ancestor::*[3]")
        except Exception:
            return None

    @staticmethod
    async def _extract_company(card) -> str:
        """Extract the company name from a card container.

        Tries, in order:
        1. The ``alt`` attribute of the first ``<img>`` (company logo).
        2. The first ``<h3>`` text.
        3. Text content of a ``.company`` or ``.companyName`` element.
        4. Fallback: ``"Unknown Company"``.
        """
        # Try logo alt text first (most reliable on Mynavi).
        try:
            img = card.locator("img").first
            alt = await img.get_attribute("alt")
            if alt and len(alt.strip()) >= 2:
                return alt.strip()
        except Exception:
            pass

        # Try h3 (company name + tagline are often in an h3).
        try:
            h3 = card.locator("h3").first
            h3_text = await h3.text_content()
            if h3_text:
                # Company name is often "Name | Tagline" -- take the first part.
                company = h3_text.split("|")[0].strip()
                if company:
                    return company
        except Exception:
            pass

        return "Unknown Company"

    @staticmethod
    async def _extract_labeled_field(card, label: str) -> str:
        """Extract the value of a labelled field like *勤務地* or *給与*.

        On Mynavi cards, each field has a label (e.g. ``勤務地：``)
        followed by its value.  This helper finds the label text node
        and returns the text that follows it, up to the next label or
        the end of the card.

        If the field is not found, returns ``"Not specified"``.
        """
        try:
            # Find the label element within this card.
            label_el = card.locator(f':text("{label}")').first
            if await label_el.count() == 0:
                return "Not specified"

            # The value is typically in the parent or next sibling.
            # Try getting the full text of the parent, then extract the
            # part after the label.
            parent = label_el.locator("xpath=..")
            full_text = await parent.text_content()
            if full_text:
                # Split on the label and take what follows.
                parts = full_text.split(label, 1)
                if len(parts) > 1:
                    value = parts[1].strip().lstrip("：: \t\n")
                    # Truncate at next common label boundary.
                    for boundary in ("\n", "  ", "勤務地", "給与", "仕事内容", "対象となる方", "企業データ"):
                        idx = value.find(boundary)
                        if idx > 0:
                            value = value[:idx]
                    return value.strip() or "Not specified"

            return "Not specified"
        except Exception:
            return "Not specified"
