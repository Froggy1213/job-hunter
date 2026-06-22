"""Green (green-japan.com) scraper.

Targets design/creative job listings in Tokyo from Green, one of the
leading Japanese platforms for IT, Web, and UI/UX design jobs.  Uses
Playwright because Green is a Next.js app that hydrates client-side.

URL structure (discovered via reconnaissance):
    Search page:
        https://www.green-japan.com/search?keyword=デザイン&page={N}

    Job detail:
        https://www.green-japan.com/company/{company_id}/job/{job_id}

    Area routes (for reference):
        /area/13  = 東京都 (Tokyo)

Card structure (per DOM analysis):
    Each job card is an ``<a href="/company/{cid}/job/{jid}">`` wrapping:
        - Company logo <img>
        - Company name + employee count + founding year
        - <h2>/<h3> job title
        - Location text (e.g. "東京都", "フルリモート")
        - Salary range text
        - Company description
        - Feature tags (フルリモート, フレックスタイム, etc.)
        - "気になる" bookmark button

    "他の{N}求人" links (other jobs at the same company) also match
    ``a[href*="/job/"]`` and must be filtered out.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.green")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.green-japan.com"
_SEARCH_KEYWORD = "デザイン"
_MAX_PAGES = 2
_PAGE_DELAY = 2.0  # seconds between pages (politeness)
_NAV_TIMEOUT = 30_000  # ms

# Job URLs: /company/{digits}/job/{digits}
_JOB_HREF_RE = re.compile(r"^/company/\d+/job/\d+")

# Secondary "他のN求人" links have the same href pattern as main cards
# but are much shorter in text content.  Main cards are rich: they
# contain company info, title, salary, description, and tags.
_MIN_CARD_TEXT_LENGTH = 80


class GreenScraper(BaseScraper):
    """Scrape design jobs in Tokyo from Green (green-japan.com).

    Overrides ``fetch_jobs()`` to manage Playwright and pagination.
    Uses the keyword ``デザイン`` (design) to filter results, which
    returns UI/UX, Web, and Graphic designer positions.
    """

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.GREEN

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate through up to ``_MAX_PAGES`` of Green search results.

        Uses the ``page`` query parameter for pagination.
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
                        "Scraping Green page",
                        extra={"page": page_num, "url": url},
                    )
                    try:
                        await page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=_NAV_TIMEOUT,
                        )

                        # Wait for job cards to appear.
                        await page.wait_for_selector(
                            'a[href*="/job/"]',
                            state="attached",
                            timeout=15_000,
                        )

                        # Allow Next.js hydration to finish.
                        await page.wait_for_timeout(1_500)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "Green page parsed",
                            extra={
                                "page": page_num,
                                "cards_found": len(jobs),
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to scrape Green page %d", page_num
                        )
                        break

                    if page_num >= _MAX_PAGES:
                        break

                    await asyncio.sleep(_PAGE_DELAY)

            finally:
                await browser.close()

        logger.info(
            "Green scrape complete",
            extra={"total_jobs": len(all_jobs)},
        )
        return all_jobs

    # ------------------------------------------------------------------
    # Page parsing
    # ------------------------------------------------------------------

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract job listings from a fully-loaded Green search page.

        Strategy
        --------

        Every job card is an ``<a href="/company/{id}/job/{id}">``.
        We locate all such links, filter out thin "他のN求人" secondary
        links (by text length), and extract the title, company, and
        location from each card's inner DOM.

        Green's cards are SSR-rendered by Next.js, so the content is
        available in the initial HTML after hydration.
        """
        cards: list[JobPosting] = []
        seen_ids: set[str] = set()

        link_elements = page.locator('a[href*="/job/"]')
        link_count = await link_elements.count()
        logger.debug("Found %d job links on page", link_count)

        for i in range(link_count):
            try:
                link = link_elements.nth(i)

                href = await link.get_attribute("href")
                if not href or not _JOB_HREF_RE.match(href):
                    continue

                # ---- Filter thin secondary links ("他のN求人") ----
                text = (await link.text_content()).strip()
                if len(text) < _MIN_CARD_TEXT_LENGTH:
                    # "他のN求人" links contain very little text (<30 chars).
                    continue

                # ---- Deduplicate by job ID ----
                job_id = _extract_job_id(href)
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                url = urljoin(_BASE_URL, href)

                # ---- title ----
                title = await self._extract_title(link)

                # ---- company ----
                company = await self._extract_company(link)

                # ---- location ----
                location = await self._extract_location(link)

                cards.append(
                    JobPosting(
                        title=title,
                        company=company,
                        url=url,  # type: ignore[arg-type]
                        location=location,
                        source_platform=self.platform,
                    )
                )
            except Exception:
                logger.warning(
                    "Failed to parse Green card %d", i, exc_info=True
                )
                continue

        return cards

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        """Build the search URL for the given 1-indexed page."""
        if page_num == 1:
            return f"{_BASE_URL}/search?keyword={_SEARCH_KEYWORD}"
        return f"{_BASE_URL}/search?keyword={_SEARCH_KEYWORD}&page={page_num}"

    # ------------------------------------------------------------------
    # Card-level extraction
    # ------------------------------------------------------------------

    @staticmethod
    async def _extract_title(card) -> str:
        """Extract the job title from a card's heading element.

        Tries ``<h2>`` then ``<h3>`` then ``<h4>``.  Falls back to the
        first 200 characters of the card text.
        """
        for tag in ("h2", "h3", "h4"):
            try:
                heading = card.locator(tag).first
                if await heading.count() > 0:
                    title = (await heading.text_content()).strip()
                    if title and len(title) >= 3:
                        return title
            except Exception:
                continue

        # Fallback: card's full text (truncated).
        try:
            text = (await card.text_content()).strip()
            return text[:200] if text else "Unknown Title"
        except Exception:
            return "Unknown Title"

    @staticmethod
    async def _extract_company(card) -> str:
        """Extract the company name from the card.

        Green cards show the company name as plain text near the top,
        accompanied by employee count (e.g. "270人") and founding year
        (e.g. "2011年設立").

        Strategy:
        1. Try ``img`` alt text (company logo).
        2. Extract the company name from the card text — it appears
           before the job title heading and is typically a legal entity
           name (ends with 株式会社, 合同会社, etc.).
        3. Fallback: ``"Unknown Company"``.
        """
        # Logo alt text.
        try:
            img = card.locator("img").first
            alt = await img.get_attribute("alt")
            if alt and len(alt.strip()) >= 2:
                return alt.strip()
        except Exception:
            pass

        # Parse the card's text for a company name pattern.
        # Green shows: "CompanyName 270人 2011年設立" early in the card.
        try:
            full_text = (await card.text_content()).strip()
            for pattern in ("株式会社", "合同会社", "有限会社", "一般社団法人"):
                idx = full_text.find(pattern)
                if idx >= 0:
                    # Extract from the start of this entity up to the pattern end.
                    start = max(0, idx - 40)
                    end = idx + len(pattern)
                    snippet = full_text[start:end]
                    # Find the company name within the snippet:
                    # it's the text segment ending with the pattern.
                    name_end = idx + len(pattern)
                    # Walk back to find the beginning (after newline or previous entity).
                    name_start = idx
                    for j in range(idx - 1, max(0, idx - 60), -1):
                        if full_text[j] in ("\n", "】", "）"):
                            name_start = j + 1
                            break
                    name = full_text[name_start:name_end].strip()
                    if name and 3 <= len(name) <= 100:
                        return name
        except Exception:
            pass

        return "Unknown Company"

    @staticmethod
    async def _extract_location(card) -> str:
        """Extract the location from the card.

        Green cards typically show the location as a short text segment
        near the salary info.  We check for common patterns.
        """
        try:
            full_text = await card.text_content()

            # Check for "フルリモート" (fully remote).
            if "フルリモート" in full_text:
                return "Tokyo (Full Remote)"

            # Check for specific wards/cities.
            tokyo_locations = [
                "渋谷", "新宿", "港区", "千代田", "目黒", "品川",
                "世田谷", "中央区", "文京", "台東", "墨田", "江東",
                "豊島", "六本木", "代々木", "恵比寿", "表参道",
                "大手町", "丸の内", "秋葉原", "赤坂", "虎ノ門",
            ]
            for loc in tokyo_locations:
                if loc in full_text:
                    return f"Tokyo, {loc}"

            # Generic Tokyo markers.
            if "東京都" in full_text:
                return "Tokyo"
            if "東京" in full_text:
                return "Tokyo"
        except Exception:
            pass

        return "Japan"


# -------------------------------------------------------------------
# Module-level helpers
# -------------------------------------------------------------------


def _extract_job_id(href: str) -> str:
    """Return the job ID from a Green job URL.

    >>> _extract_job_id("/company/3111/job/321519")
    '321519'
    >>> _extract_job_id("/company/45/job/312546?page=1")
    '312546'
    """
    path = href.split("?")[0]
    # Pattern: /company/{cid}/job/{jid}
    parts = path.rstrip("/").split("/")
    # parts = ["", "company", "{cid}", "job", "{jid}"]
    return parts[-1] if len(parts) >= 5 else ""
