"""Indeed Japan (jp.indeed.com) scraper.

Targets design/creative job listings in Tokyo from Indeed, the world's
largest job search engine.  Indeed aggressively blocks bots -- this
scraper uses multiple anti-detection techniques and prioritises
extracting the embedded ``mosaic.providerData`` JSON over fragile
CSS selectors.

URL structure:
    Search page:
        https://jp.indeed.com/jobs
        ?q=UI+UX+Designer+Graphic+Design
        &l=Tokyo

    Pagination (offset-based, 10 per page):
        .../jobs?q=...&l=Tokyo&start=10   (page 2)
        .../jobs?q=...&l=Tokyo&start=20   (page 3)

    Job detail (multiple patterns):
        /pagead/clk?mo=r&ad=...&vjs=3     (sponsored/agency postings)
        /viewjob?jk={jobkey}              (direct Indeed listings)

Embedded JSON (primary extraction method):
    Indeed embeds all search result data in:
        window.mosaic.providerData["mosaic-provider-jobcards"]

    This JSON object contains: jobkey, title, company,
    formattedLocation, snippet, viewJobLink, salarySnippet,
    formattedRelativeTime -- everything we need.

Anti-bot measures implemented:
    - Random delays before/after navigation (1–3s)
    - Human-like scrolling (gradual, varied scroll steps)
    - Circuit breaker: stop after consecutive empty pages
    - Single-page max by default (reduces detection surface)
    - Realistic viewport size + Japanese locale + timezone
    - No request interception (Indeed detects blocked resources)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.indeed")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://jp.indeed.com"
_SEARCH_PATH = "/jobs"
_SEARCH_QUERY = "UI UX Designer Graphic Design"
_SEARCH_LOCATION = "Tokyo"
_JOBS_PER_PAGE = 10  # Indeed returns 10 results per page

# Anti-bot: very conservative limits.
_MAX_PAGES = 1  # Start with 1 page -- Indeed is extremely trigger-happy.
_PAGE_DELAY_MIN = 2.0
_PAGE_DELAY_MAX = 4.0
_NAV_TIMEOUT = 30_000  # ms
_SCROLL_STEPS = 4  # Gradual scrolling passes

# JSON extraction regex -- matches the mosaic provider data block.
_MOSAIC_RE = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});',
    re.DOTALL,
)

# Circuit breaker: stop scraping after this many consecutive empty pages.
_MAX_CONSECUTIVE_EMPTY = 2


class IndeedScraper(BaseScraper):
    """Scrape design jobs in Tokyo from Indeed Japan.

    Overrides ``fetch_jobs()`` with anti-bot countermeasures:
    random delays, human-like scrolling, and a circuit breaker.
    Primary extraction uses the embedded JSON; falls back to DOM
    selectors if the JSON block is absent.
    """

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._consecutive_empty = 0

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.INDEED

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate Indeed search results with anti-bot countermeasures.

        Returns an empty list gracefully if Cloudflare blocks the
        request or a CAPTCHA appears.
        """
        all_jobs: list[JobPosting] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self._headless,
                args=[
                    "--window-size=1920,1080",
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                context = await browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=self._user_agent,
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                for page_num in range(_MAX_PAGES):
                    start_offset = page_num * _JOBS_PER_PAGE
                    url = self._build_page_url(start_offset)

                    logger.info(
                        "Scraping Indeed page",
                        extra={"page": page_num + 1, "offset": start_offset},
                    )

                    # --- Pre-navigation delay (random) ---
                    await asyncio.sleep(random.uniform(1.0, 3.0))

                    try:
                        await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=_NAV_TIMEOUT,
                        )
                    except Exception:
                        logger.warning(
                            "Indeed navigation failed (possible block)",
                            extra={"url": url},
                        )
                        self._consecutive_empty += 1
                        break

                    # --- Post-navigation breathing room ---
                    await asyncio.sleep(random.uniform(1.5, 3.0))

                    # --- Check for block / CAPTCHA ---
                    if await self._is_blocked(page):
                        logger.warning(
                            "Indeed returned a block page or CAPTCHA -- aborting"
                        )
                        break

                    # --- Human-like scrolling ---
                    await self._scroll_like_human(page)

                    # --- Let lazy-loaded cards render ---
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                    # --- Parse ---
                    try:
                        jobs = await self.parse_page(page)
                    except Exception:
                        logger.exception("Failed to parse Indeed page")
                        jobs = []

                    if not jobs:
                        self._consecutive_empty += 1
                        logger.warning(
                            "Indeed page returned 0 jobs (empty=%d)",
                            self._consecutive_empty,
                        )
                    else:
                        self._consecutive_empty = 0
                        all_jobs.extend(jobs)
                        logger.info(
                            "Indeed page parsed",
                            extra={
                                "page": page_num + 1,
                                "cards_found": len(jobs),
                            },
                        )

                    # Circuit breaker.
                    if self._consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                        logger.warning(
                            "Circuit breaker tripped after %d empty pages",
                            self._consecutive_empty,
                        )
                        break

                    # Random inter-page delay.
                    if page_num < _MAX_PAGES - 1:
                        await asyncio.sleep(
                            random.uniform(_PAGE_DELAY_MIN, _PAGE_DELAY_MAX)
                        )

            finally:
                await browser.close()

        logger.info(
            "Indeed scrape complete",
            extra={"total_jobs": len(all_jobs)},
        )
        return all_jobs

    # ------------------------------------------------------------------
    # Page parsing
    # ------------------------------------------------------------------

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract job listings from a loaded Indeed search page.

        Strategy (two-tier):

        1. **JSON extraction** (primary): Indeed embeds a ``mosaic.providerData``
           JSON blob in the page that contains ALL job card data.  We extract
           and parse this directly -- it's faster, more reliable, and immune
           to CSS class churn.

        2. **DOM fallback** (secondary): If the JSON block is missing (rare
           on older page variants), we fall back to ``[data-jk]`` attribute
           selectors and individual field extraction.
        """
        # ---- Tier 1: JSON extraction ----
        try:
            html = await page.content()
            jobs = self._parse_mosaic_json(html)
            if jobs:
                logger.debug(
                    "Extracted %d jobs from mosaic JSON", len(jobs)
                )
                return jobs
        except Exception:
            logger.debug("Mosaic JSON extraction failed, falling back to DOM")

        # ---- Tier 2: DOM fallback ----
        return await self._parse_dom(page)

    # ------------------------------------------------------------------
    # Tier 1: mosaic.providerData JSON extraction
    # ------------------------------------------------------------------

    def _parse_mosaic_json(self, html: str) -> list[JobPosting]:
        """Parse the embedded ``mosaic.providerData`` JSON blob.

        The JSON structure is:
            {
              "metaData": {
                "mosaicProviderJobCardsModel": {
                  "results": [
                    {
                      "jobkey": "...",
                      "title": "...",
                      "company": "...",
                      "formattedLocation": "...",
                      "snippet": "...",
                      "viewJobLink": "...",
                      "salarySnippet": {...},
                      "formattedRelativeTime": "...",
                      "sponsored": false
                    },
                    ...
                  ]
                }
              }
            }
        """
        match = _MOSAIC_RE.search(html)
        if not match:
            logger.debug("mosaic.providerData block not found in page HTML")
            return []

        data = json.loads(match.group(1))
        results = (
            data.get("metaData", {})
            .get("mosaicProviderJobCardsModel", {})
            .get("results", [])
        )

        if not results:
            return []

        cards: list[JobPosting] = []
        seen_keys: set[str] = set()

        for item in results:
            try:
                jobkey = item.get("jobkey", "")
                if not jobkey or jobkey in seen_keys:
                    continue
                seen_keys.add(jobkey)

                title = (item.get("title") or "Unknown Title").strip()
                company = (item.get("company") or "Unknown Company").strip()
                location = (
                    item.get("formattedLocation") or "Tokyo"
                ).strip()

                # Build the absolute job URL.
                view_link = item.get("viewJobLink", "")
                if view_link:
                    url = urljoin(_BASE_URL, view_link)
                else:
                    url = f"{_BASE_URL}/viewjob?jk={jobkey}"

                # Salary snippet (optional).
                salary_info = item.get("salarySnippet")
                salary = None
                if salary_info and isinstance(salary_info, dict):
                    salary = (
                        salary_info.get("text")
                        or salary_info.get("salaryText")
                    )
                elif isinstance(salary_info, str):
                    salary = salary_info

                cards.append(
                    JobPosting(
                        title=title[:500],
                        company=company[:250],
                        url=url,  # type: ignore[arg-type]
                        location=location[:250],
                        source_platform=self.platform,
                        salary=salary[:250] if salary else None,
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to map Indeed JSON item", exc_info=True
                )
                continue

        return cards

    # ------------------------------------------------------------------
    # Tier 2: DOM fallback
    # ------------------------------------------------------------------

    async def _parse_dom(self, page: Page) -> list[JobPosting]:
        """Fallback: extract jobs from the DOM using ``[data-jk]`` selectors.

        This is less reliable than JSON extraction because Indeed
        frequently changes CSS class names.  We rely on ``data-jk``
        attributes which are more stable.
        """
        cards: list[JobPosting] = []
        seen_keys: set[str] = set()

        try:
            # Wait for at least one card.
            await page.wait_for_selector(
                '[data-jk]',
                state="attached",
                timeout=10_000,
            )
        except Exception:
            logger.debug("No [data-jk] elements found on Indeed page")
            return cards

        card_elements = page.locator("[data-jk]")
        card_count = await card_elements.count()
        logger.debug("Found %d [data-jk] elements via DOM", card_count)

        for i in range(card_count):
            try:
                el = card_elements.nth(i)
                jobkey = await el.get_attribute("data-jk")
                if not jobkey or jobkey in seen_keys:
                    continue
                seen_keys.add(jobkey)

                # Title: h2.jobTitle or h2 a
                title = await self._dom_extract(el, [
                    "h2 a", "h2", "a[data-jk]",
                ])

                # Company: span.companyName or [data-testid="company-name"]
                company = await self._dom_extract(el, [
                    '[data-testid="company-name"]',
                    'span.companyName',
                    'span[class*="company"]',
                ])

                # Location: div.companyLocation or [data-testid="text-location"]
                location = await self._dom_extract(el, [
                    '[data-testid="text-location"]',
                    'div.companyLocation',
                    'div[class*="location"]',
                ])

                # URL: prefer viewjob link.
                url = await self._dom_extract_href(el, [
                    'a[href*="/viewjob"]',
                    'a[data-jk]',
                ])

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
                logger.debug(
                    "Failed to parse Indeed DOM card %d", i, exc_info=True
                )
                continue

        return cards

    @staticmethod
    async def _dom_extract(container, selectors: list[str]) -> str:
        """Try each *selector* on *container*, return the first non-empty text."""
        for sel in selectors:
            try:
                el = container.locator(sel).first
                if await el.count() > 0:
                    text = (await el.text_content()).strip()
                    if text:
                        return text[:500]
            except Exception:
                continue
        return "Unknown"

    @staticmethod
    async def _dom_extract_href(container, selectors: list[str]) -> str:
        """Try each *selector*, return first absolute URL from its ``href``."""
        for sel in selectors:
            try:
                el = container.locator(sel).first
                if await el.count() > 0:
                    href = await el.get_attribute("href")
                    if href:
                        return urljoin(_BASE_URL, href)
            except Exception:
                continue
        return _BASE_URL

    # ------------------------------------------------------------------
    # Anti-bot helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_page_url(start_offset: int) -> str:
        """Build the Indeed search URL with an optional ``start`` offset.

        Page 1:  start=0   (or omitted)
        Page 2:  start=10
        Page 3:  start=20
        """
        base = (
            f"{_BASE_URL}{_SEARCH_PATH}"
            f"?q={_SEARCH_QUERY.replace(' ', '+')}"
            f"&l={_SEARCH_LOCATION}"
        )
        if start_offset > 0:
            return f"{base}&start={start_offset}"
        return base

    @staticmethod
    async def _is_blocked(page: Page) -> bool:
        """Detect common block/CAPTCHA signals on the page.

        Checks for:
        - Cloudflare Turnstile / challenge page.
        - Indeed's "verify you are human" interstitial.
        - Empty body (blocked before content loaded).
        """
        try:
            body_text = await page.text_content("body")
            if not body_text or len(body_text) < 100:
                return True

            block_signals = [
                "verify you are human",
                "are you a robot",
                "captcha",
                "access denied",
                "blocked",
                "cf-challenge",
                "turnstile",
                "please verify",
                "アクセスをブロック",
                "robot check",
            ]
            lower = body_text.lower()
            for signal in block_signals:
                if signal in lower:
                    return True
        except Exception:
            return True

        return False

    @staticmethod
    async def _scroll_like_human(page: Page) -> None:
        """Gradually scroll down the page to simulate human reading behaviour.

        Multiple small scroll steps with random pauses trigger lazy-loaded
        content and reduce bot-detection signals.
        """
        try:
            for step in range(_SCROLL_STEPS):
                # Scroll a fraction of the viewport height.
                scroll_by = random.randint(200, 500)
                await page.evaluate(f"window.scrollBy(0, {scroll_by})")
                await asyncio.sleep(random.uniform(0.3, 0.9))
        except Exception:
            # Scrolling failed (page may have closed) -- non-fatal.
            pass
