"""GaijinPot Jobs scraper.

Targets design/creative job listings in Tokyo from GaijinPot Jobs,
the leading platform for foreigners seeking work in Japan.  Uses
Playwright because even though GaijinPot is largely server-rendered,
some listing enhancements (badges, modals) rely on JS.

URL structure (discovered via reconnaissance):
    Search page:
        https://jobs.gaijinpot.com/en/job
        ?region=22          (Kanto region, includes Tokyo)
        &keywords=design
        &page={N}

    Job detail:
        https://jobs.gaijinpot.com/en/job/{numeric_id}

    Company page:
        https://jobs.gaijinpot.com/en/organization/{org_id}

Card structure (per DOM analysis):
    Each listing card contains:
        - Employment type badge (Full Time / Part Time / Contract)
        - Optional status badges (New, Remote work, Watch video)
        - Company logo <img> with alt text = company name
        - Job title as a linked heading → ``/en/job/{id}``
        - Company name field
        - Location + salary rows

    The page is server-rendered HTML with JS enhancements, so content
    is available immediately after ``networkidle``.
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

logger = logging.getLogger("job_hunter.scraper.gaijinpot")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://jobs.gaijinpot.com"
_SEARCH_PATH = "/en/job"
_MAX_PAGES = 2
_PAGE_DELAY = 2.0  # seconds between pages (politeness)
_NAV_TIMEOUT = 30_000  # ms

# Job detail URLs: /en/job/{digits}
_JOB_HREF_RE = re.compile(r"^/en/job/\d+")

# Search region: 22 = Kanto (includes Tokyo).
# We add the keyword "design" to filter for creative/design/IT roles.
_SEARCH_KEYWORDS = "design"
_SEARCH_REGION = 22


class GaijinPotScraper(BaseScraper):
    """Scrape design jobs in Tokyo from GaijinPot Jobs.

    Uses the ``region=22`` (Kanto) filter plus ``keywords=design`` to
    return English-friendly design roles in the Tokyo area.  Pagination
    is handled via the ``page`` query parameter.
    """

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.GAIJINPOT

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate through up to ``_MAX_PAGES`` of GaijinPot search results."""
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
                        "Scraping GaijinPot page",
                        extra={"page": page_num, "url": url},
                    )
                    try:
                        await page.goto(
                            url,
                            wait_until="networkidle",
                            timeout=_NAV_TIMEOUT,
                        )

                        # Wait for job title links to appear.
                        await page.wait_for_selector(
                            'a[href*="/en/job/"]',
                            state="attached",
                            timeout=15_000,
                        )

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "GaijinPot page parsed",
                            extra={
                                "page": page_num,
                                "cards_found": len(jobs),
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to scrape GaijinPot page %d", page_num
                        )
                        break

                    if page_num >= _MAX_PAGES:
                        break

                    await asyncio.sleep(_PAGE_DELAY)

            finally:
                await browser.close()

        logger.info(
            "GaijinPot scrape complete",
            extra={"total_jobs": len(all_jobs)},
        )
        return all_jobs

    # ------------------------------------------------------------------
    # Page parsing
    # ------------------------------------------------------------------

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract job listings from a fully-loaded GaijinPot search page.

        Strategy
        --------

        Every job card contains an ``<a href="/en/job/{id}">`` for the
        job title.  We locate all such numeric-ID links, filter out
        navigation/pagination links (which are short text), and extract
        the title, company, and location from each card.

        GaijinPot cards have a distinct pattern: the job title link is
        typically a heading element containing the full role name.
        Company name appears both as the logo ``alt`` text and as a
        separate labelled field.
        """
        cards: list[JobPosting] = []
        seen_ids: set[str] = set()

        link_elements = page.locator('a[href*="/en/job/"]')
        link_count = await link_elements.count()
        logger.debug("Found %d /en/job/ links on page", link_count)

        for i in range(link_count):
            try:
                link = link_elements.nth(i)

                href = await link.get_attribute("href")
                if not href or not _JOB_HREF_RE.match(href):
                    continue

                # ---- Filter thin links (pagination, nav, "Next") ----
                text = (await link.text_content()).strip()
                if len(text) < 10:
                    # Pagination links like "1", "2", "Next" are thin.
                    # Job title links contain the full role name.
                    continue

                # ---- Deduplicate by job ID ----
                job_id = _extract_job_id(href)
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                url = urljoin(_BASE_URL, href)

                # ---- Resolve the card container ----
                card = _resolve_card_container(link)

                # ---- title ----
                title = await _extract_title(link)

                # ---- company ----
                company = await _extract_company(card, link)

                # ---- location ----
                location = await _extract_location(card)

                # ---- salary (optional enrichment) ----
                salary = await _extract_salary(card)

                cards.append(
                    JobPosting(
                        title=title,
                        company=company,
                        url=url,  # type: ignore[arg-type]
                        location=location,
                        source_platform=self.platform,
                        salary=salary,
                    )
                )
            except Exception:
                logger.warning(
                    "Failed to parse GaijinPot card %d", i, exc_info=True
                )
                continue

        return cards

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        """Build the search URL for the given 1-indexed page number."""
        base = (
            f"{_BASE_URL}{_SEARCH_PATH}"
            f"?region={_SEARCH_REGION}"
            f"&keywords={_SEARCH_KEYWORDS}"
        )
        if page_num > 1:
            return f"{base}&page={page_num}"
        return base


# -------------------------------------------------------------------
# Card-level extraction (module-level so they're easy to unit-test)
# -------------------------------------------------------------------


def _resolve_card_container(link):
    """Walk up from *link* to find the enclosing card element.

    GaijinPot uses a table-like or grid layout.  We try:
    1. ``xpath=ancestor::article[1]`` — semantic card
    2. ``xpath=ancestor::tr[1]`` — table row pattern
    3. ``xpath=ancestor::li[1]`` — list item
    4. ``xpath=ancestor::div[contains(@class,'job')][1]`` — job class
    5. ``xpath=ancestor::*[4]`` — great-grandparent fallback
    """
    for selector in (
        "xpath=ancestor::article[1]",
        "xpath=ancestor::tr[1]",
        "xpath=ancestor::li[1]",
        "xpath=ancestor::div[contains(@class,'job')][1]",
    ):
        try:
            el = link.locator(selector)
            return el
        except Exception:
            continue

    try:
        return link.locator("xpath=ancestor::*[3]")
    except Exception:
        return link  # Last resort: use the link itself


async def _extract_title(link) -> str:
    """Extract the job title from the link element.

    The job title link is the main heading of the card.  We use the
    link's own text content, which on GaijinPot is the full job title.
    """
    try:
        title = (await link.text_content()).strip()
        if title and len(title) >= 3:
            # Clean up: GaijinPot sometimes prefixes with badges like
            # "【Remote】" — keep those as part of the title.
            return title[:300]
    except Exception:
        pass
    return "Unknown Title"


async def _extract_company(card, link) -> str:
    """Extract the company name from the card.

    Strategy:
    1. ``img`` ``alt`` attribute — GaijinPot sets alt text on company logos.
    2. Text node containing "Company:" or similar.
    3. A link to ``/en/organization/{id}`` text content.
    4. Fallback: ``"Unknown Company"``.
    """
    # Logo alt text (most reliable on GaijinPot).
    try:
        imgs = card.locator("img")
        img_count = await imgs.count()
        for i in range(img_count):
            alt = await imgs.nth(i).get_attribute("alt")
            if alt and len(alt.strip()) >= 2:
                # Exclude generic/placeholder images.
                if alt.strip().lower() not in ("logo", "company logo", "image", ""):
                    return alt.strip()
    except Exception:
        pass

    # Organization link within the card.
    try:
        org_link = card.locator('a[href*="/en/organization/"]').first
        if await org_link.count() > 0:
            name = (await org_link.text_content()).strip()
            if name and len(name) >= 2:
                return name
    except Exception:
        pass

    return "Unknown Company"


async def _extract_location(card) -> str:
    """Extract the location from the card text.

    GaijinPot typically shows the location as a short field (e.g.
    "Tokyo", "Tokyo, Minato-ku", "Yokohama, Kanagawa").  We search
    the card text for common location patterns.
    """
    try:
        full_text = await card.text_content()

        # Tokyo ward names (common on GaijinPot listings).
        tokyo_wards = [
            "Minato", "Shibuya", "Shinjuku", "Chiyoda", "Meguro",
            "Shinagawa", "Setagaya", "Bunkyo", "Taito", "Sumida",
            "Koto", "Toshima", "Roppongi", "Ebisu", "Akasaka",
            "Otemachi", "Marunouchi", "Akihabara", "Shimbashi",
            "Ginza", "Nihonbashi", "Shinagawa",
        ]
        for ward in tokyo_wards:
            if ward.lower() in full_text.lower():
                return f"Tokyo, {ward}"

        # Generic Tokyo markers.
        if "tokyo" in full_text.lower():
            # Try to extract the specific area.
            return "Tokyo"
        if "東京都" in full_text or "東京" in full_text:
            return "Tokyo"

        # Other Japanese cities.
        other_cities = ["Osaka", "Kyoto", "Yokohama", "Nagoya", "Fukuoka", "Sapporo"]
        for city in other_cities:
            if city.lower() in full_text.lower():
                return city

    except Exception:
        pass

    return "Japan"


async def _extract_salary(card) -> str | None:
    """Attempt to extract salary information from the card.

    GaijinPot often shows salary as a labelled field.  We look for
    common patterns: "¥", "JPY", or numeric ranges like "3.5M - 5M".
    """
    try:
        full_text = await card.text_content()

        # Japanese yen symbol or salary range patterns.
        if "¥" in full_text:
            # Find the segment containing ¥.
            idx = full_text.find("¥")
            segment = full_text[max(0, idx - 5):idx + 40]
            # Trim to a reasonable salary string.
            for delim in ("\n", "  ", " / ", "Location", "Company"):
                d = segment.find(delim)
                if d > 5:
                    segment = segment[:d]
            salary = segment.strip()
            if salary and 2 <= len(salary) <= 60:
                return salary

        # "M" ranges (e.g. "3.5M - 5M JPY").
        import re as _re
        m = _re.search(r"[\d.]+M\s*[-–~]\s*[\d.]+M", full_text)
        if m:
            return m.group(0)

    except Exception:
        pass

    return None


def _extract_job_id(href: str) -> str:
    """Return the numeric job ID from a GaijinPot job URL.

    >>> _extract_job_id("/en/job/159034")
    '159034'
    >>> _extract_job_id("/en/job/159050?some=param")
    '159050'
    """
    path = href.split("?")[0]
    return path.rstrip("/").split("/")[-1]
