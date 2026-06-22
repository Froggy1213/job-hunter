"""Wantedly (ウォンテッドリー) scraper.

Targets design/creative project listings in Tokyo from Wantedly,
the leading Japanese startup/tech job platform.  Uses Playwright
because Wantedly is a React SPA that hydrates client-side.

URL structure (discovered via reconnaissance):
    Search page:
        https://www.wantedly.com/projects
        ?type=mixed
        &page={N}
        &occupations=ui_ux_designer,web_designer,graphic_designer
        &locations=tokyo

    Project detail:
        https://www.wantedly.com/projects/{numeric_id}

    Company page:
        https://www.wantedly.com/companies/{slug}

Card structure (per DOM analysis):
    Each project is an ``<a href="/projects/{id}">`` wrapping:
        - <img> thumbnail
        - <li> tags (occupation / entry count)
        - <h3> project title
        - <a href="/companies/{slug}"> company name + logo
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

logger = logging.getLogger("job_hunter.scraper.wantedly")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.wantedly.com"

# Occupation IDs used by Wantedly's internal filter.
# These map to: UI/UX Designer, Web Designer, Graphic Designer.
_DESIGN_OCCUPATIONS = "ui_ux_designer,web_designer,graphic_designer"

_MAX_PAGES = 2
_PAGE_DELAY = 2.5  # seconds between pages (politeness — Wantedly is strict)
_NAV_TIMEOUT = 30_000  # ms

# Project URLs are purely numeric IDs: /projects/123456
_PROJECT_HREF_RE = re.compile(r"^/projects/\d+")

# "featured=0" indicates the card appeared in a featured/pickup slot.
# These are duplicates of cards already in the main listing.
_FEATURED_DUPE_RE = re.compile(r"[?&]featured=0")


class WantedlyScraper(BaseScraper):
    """Scrape design/creative projects in Tokyo from Wantedly.

    Overrides ``fetch_jobs()`` to manage Playwright and pagination.
    Each page is parsed by ``parse_page()``, which locates project
    cards via their stable ``/projects/{id}`` URL pattern.
    """

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.WANTEDLY

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate through up to ``_MAX_PAGES`` of Wantedly search results.

        Uses the ``page`` query parameter for pagination — more reliable
        than clicking a React-controlled "Load More" button.
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
                        "Scraping Wantedly page",
                        extra={"page": page_num, "url": url},
                    )
                    try:
                        await page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=_NAV_TIMEOUT,
                        )

                        # Wait for React hydration — h3 cards must be present.
                        await page.wait_for_selector(
                            'a[href^="/projects/"] h3',
                            state="attached",
                            timeout=15_000,
                        )

                        # Let React finish rendering any deferred list items.
                        await page.wait_for_timeout(1_500)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "Wantedly page parsed",
                            extra={
                                "page": page_num,
                                "cards_found": len(jobs),
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to scrape Wantedly page %d", page_num
                        )
                        break

                    if page_num >= _MAX_PAGES:
                        break

                    await asyncio.sleep(_PAGE_DELAY)

            finally:
                await browser.close()

        logger.info(
            "Wantedly scrape complete",
            extra={"total_jobs": len(all_jobs)},
        )
        return all_jobs

    # ------------------------------------------------------------------
    # Page parsing
    # ------------------------------------------------------------------

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract project listings from a fully-loaded Wantedly page.

        Strategy
        --------

        Every project card is an ``<a href="/projects/{id}">``.  We
        collect all such links, filter to numeric-ID-only paths, skip
        ``featured=0`` duplicates, and extract the title (``<h3>``),
        company name (``a[href^="/companies/"]``), and location from
        each card's inner DOM.
        """
        cards: list[JobPosting] = []
        seen_ids: set[str] = set()

        link_elements = page.locator('a[href^="/projects/"]')
        link_count = await link_elements.count()
        logger.debug("Found %d project links on page", link_count)

        for i in range(link_count):
            try:
                link = link_elements.nth(i)

                href = await link.get_attribute("href")
                if not href:
                    continue

                # Must be a numeric project ID (not /projects/new, /projects/drafts).
                if not _PROJECT_HREF_RE.match(href):
                    continue

                # Skip featured/pickup duplicates.
                if _FEATURED_DUPE_RE.search(href):
                    continue

                # Deduplicate by project ID.
                project_id = _extract_project_id(href)
                if project_id in seen_ids:
                    continue
                seen_ids.add(project_id)

                url = urljoin(_BASE_URL, href)

                # --- title (h3 inside the card) ---
                title = await self._extract_title(link)

                # --- company ---
                company = await self._extract_company(link)

                # --- location ---
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
                    "Failed to parse Wantedly card %d", i, exc_info=True
                )
                continue

        return cards

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        """Build the search URL for the given 1-indexed page number."""
        return (
            f"{_BASE_URL}/projects"
            f"?type=mixed"
            f"&page={page_num}"
            f"&occupations={_DESIGN_OCCUPATIONS}"
            f"&locations=tokyo"
        )

    # ------------------------------------------------------------------
    # Card-level extraction
    # ------------------------------------------------------------------

    @staticmethod
    async def _extract_title(card) -> str:
        """Extract the project title from the ``<h3>`` inside the card link.

        Falls back to the link's full text content if no ``h3`` is found.
        """
        try:
            h3 = card.locator("h3").first
            if await h3.count() > 0:
                title = (await h3.text_content()).strip()
                if title and len(title) >= 2:
                    return title
        except Exception:
            pass

        # Fallback: use the link text itself.
        try:
            text = (await card.text_content()).strip()
            # The card text is usually "tags... TITLE ... company info".
            # Take the first substantial line as the title.
            return text[:200]
        except Exception:
            return "Unknown Title"

    @staticmethod
    async def _extract_company(card) -> str:
        """Extract the company name from the card.

        Tries, in order:
        1. ``a[href^="/companies/"]`` text content — the company link.
        2. ``img`` alt attribute (company logo).
        3. Fallback: ``"Unknown Company"``.
        """
        # Company link — most reliable.
        try:
            company_link = card.locator('a[href^="/companies/"]').first
            if await company_link.count() > 0:
                name = (await company_link.text_content()).strip()
                if name and len(name) >= 1:
                    return name
        except Exception:
            pass

        # Logo alt text.
        try:
            img = card.locator("img").first
            alt = await img.get_attribute("alt")
            if alt and len(alt.strip()) >= 2:
                return alt.strip()
        except Exception:
            pass

        return "Unknown Company"

    @staticmethod
    async def _extract_location(card) -> str:
        """Extract the location from the card.

        Wantedly cards don't always show location explicitly in the list view.
        Since we're filtering by ``locations=tokyo`` in the URL, all results
        are in Tokyo.  We return a sensible default and attempt to extract
        more specific info if present.
        """
        # Try to find location text within the card.
        # Wantedly sometimes shows it as a tag or inline text.
        try:
            full_text = (await card.text_content()).lower()
            # Check for common Tokyo ward names.
            tokyo_wards = [
                "渋谷", "新宿", "港区", "千代田", "目黒", "品川",
                "世田谷", "中央区", "文京", "台東", "墨田", "江東",
                "豊島", "六本木", "代々木", "恵比寿", "表参道",
            ]
            for ward in tokyo_wards:
                if ward in full_text:
                    return f"Tokyo, {ward}"
        except Exception:
            pass

        # Default — the URL filter already guarantees Tokyo.
        return "Tokyo"


# -------------------------------------------------------------------
# Module-level helpers
# -------------------------------------------------------------------


def _extract_project_id(href: str) -> str:
    """Return the numeric project ID from ``/projects/{id}`` paths.

    >>> _extract_project_id("/projects/123456?featured=0")
    '123456'
    >>> _extract_project_id("/projects/2119215")
    '2119215'
    """
    # Strip trailing query string.
    path = href.split("?")[0]
    # Last segment is the ID.
    return path.rstrip("/").split("/")[-1]
