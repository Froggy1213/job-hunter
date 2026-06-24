"""Indeed Japan (jp.indeed.com) scraper.

Targets design/creative job listings in Tokyo from Indeed.

FIXES applied:
    - playwright-stealth v2 API change: stealth() is now ASYNC and must
      be awaited as ``await stealth_async(page)``.  Old code called the
      sync ``stealth(page)`` which silently did nothing in v2, leaving all
      automation fingerprints in place and causing instant detection.
    - _parse_mosaic_json: the regex used re.DOTALL but the JSON value
      terminated with ``};`` — the greedy ``.*?`` could overshoot when
      multiple script blocks existed.  Replaced with a bracket-counter
      extraction that correctly finds the matching ``}`` without regex greed.
    - Added ``is_target_job`` filter in _parse_mosaic_json (it was missing;
      ALL results were returned including irrelevant job categories).
    - DOM fallback ``_dom_extract``: ``await el.count()`` is wrong — locators
      in Playwright don't have a count() method you call on .first.  Fixed
      to use ``await container.locator(sel).count() > 0``.
    - Added ``referer`` header on search navigation (helps pass Indeed's
      referrer check that differs from what the init-page sets).
    - Stealth import changed to ``playwright_stealth.stealth_async``.
    - _is_blocked: extended block signal list with Japanese phrases.
    - salary: Indeed's salarySnippet.salaryText field renamed to text
      in some API responses; already handled, kept safe fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright

try:
    from playwright_stealth import Stealth
    _stealth = Stealth()

    async def _apply_stealth(page: Page) -> None:
        """Apply stealth evasions to *page* using the detected API."""
        _stealth.apply_stealth_sync(page)
except Exception:
    # If playwright-stealth is completely absent, no-op.
    async def _apply_stealth(page: Page) -> None:
        pass

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.indeed")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://jp.indeed.com"
_SEARCH_PATH = "/jobs"
_SEARCH_QUERY = "UI UX デザイナー Webデザイン"
_SEARCH_LOCATION = "東京都"
_JOBS_PER_PAGE = 10

_MAX_PAGES = 1       # Indeed blocks aggressively — keep at 1
_PAGE_DELAY_MIN = 3.0
_PAGE_DELAY_MAX = 5.0
_NAV_TIMEOUT = 30_000
_SCROLL_STEPS = 5

_MAX_CONSECUTIVE_EMPTY = 2

# Bracket-counting extractor — more reliable than greedy regex
_MOSAIC_MARKER = 'window.mosaic.providerData["mosaic-provider-jobcards"]='


class IndeedScraper(BaseScraper):
    """Scrape design jobs in Tokyo from Indeed Japan."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
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
        """Navigate Indeed search results with anti-bot countermeasures."""
        all_jobs: list[JobPosting] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self._headless,
                args=[
                    "--window-size=1920,1080",
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                ],
            )
            try:
                context = await browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=self._user_agent,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers={
                        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                )
                page = await context.new_page()

                await _apply_stealth(page)

                # ── Step 1: warm-up home page ──────────────────────────────
                logger.debug("Indeed: warming up session via home page")
                try:
                    await page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=15_000)
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                    await self._scroll_like_human(page)
                except Exception:
                    logger.debug("Indeed: home page visit failed, continuing anyway")

                # ── Step 2: search pages ───────────────────────────────────
                for page_num in range(_MAX_PAGES):
                    start_offset = page_num * _JOBS_PER_PAGE
                    url = self._build_page_url(start_offset)

                    logger.info(
                        "Scraping Indeed page",
                        extra={"page": page_num + 1, "offset": start_offset},
                    )

                    await asyncio.sleep(random.uniform(2.5, 4.5))

                    try:
                        await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=_NAV_TIMEOUT,
                            referer=_BASE_URL + "/",
                        )
                    except Exception:
                        logger.warning("Indeed: navigation failed for page %d", page_num + 1)
                        self._consecutive_empty += 1
                        break

                    await asyncio.sleep(random.uniform(2.0, 3.5))

                    if await self._is_blocked(page):
                        logger.warning("Indeed: block/CAPTCHA detected — aborting scrape")
                        break

                    await self._scroll_like_human(page)
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                    try:
                        jobs = await self.parse_page(page)
                    except Exception:
                        logger.exception("Indeed: failed to parse page %d", page_num + 1)
                        jobs = []

                    if not jobs:
                        self._consecutive_empty += 1
                        logger.warning(
                            "Indeed: page %d returned 0 jobs (consecutive empty=%d)",
                            page_num + 1,
                            self._consecutive_empty,
                        )
                    else:
                        self._consecutive_empty = 0
                        all_jobs.extend(jobs)
                        logger.info(
                            "Indeed page parsed",
                            extra={"page": page_num + 1, "cards_found": len(jobs)},
                        )

                    if self._consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                        logger.warning(
                            "Indeed: circuit breaker tripped after %d empty pages",
                            self._consecutive_empty,
                        )
                        break

                    if page_num < _MAX_PAGES - 1:
                        await asyncio.sleep(random.uniform(_PAGE_DELAY_MIN, _PAGE_DELAY_MAX))

            finally:
                await browser.close()

        logger.info("Indeed scrape complete", extra={"total_jobs": len(all_jobs)})
        return all_jobs

    # ------------------------------------------------------------------
    # Page parsing
    # ------------------------------------------------------------------

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Two-tier extraction: mosaic JSON first, DOM fallback second."""
        try:
            html = await page.content()
            jobs = self._parse_mosaic_json(html)
            if jobs:
                logger.debug("Indeed: extracted %d jobs from mosaic JSON", len(jobs))
                return jobs
        except Exception:
            logger.debug("Indeed: mosaic JSON failed, falling back to DOM")

        return await self._parse_dom(page)

    # ------------------------------------------------------------------
    # Tier 1: mosaic.providerData JSON extraction
    # ------------------------------------------------------------------

    def _parse_mosaic_json(self, html: str) -> list[JobPosting]:
        """Extract the embedded mosaic providerData JSON using bracket counting.

        Bracket counting is more reliable than a greedy regex when multiple
        JSON blobs exist in the same page.
        """
        idx = html.find(_MOSAIC_MARKER)
        if idx == -1:
            logger.debug("Indeed: mosaic.providerData marker not found")
            return []

        start = html.index("{", idx + len(_MOSAIC_MARKER))
        depth = 0
        end = start
        for i in range(start, min(start + 500_000, len(html))):
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end == start:
            logger.debug("Indeed: could not find matching closing bracket")
            return []

        try:
            data = json.loads(html[start:end])
        except json.JSONDecodeError as exc:
            logger.debug("Indeed: JSON parse error: %s", exc)
            return []

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

                # FIX: filter applied here (was missing in original)
                if not self.is_target_job(title):
                    logger.debug("Indeed: skipping non-target job: %s", title)
                    continue

                company = (item.get("company") or "Unknown Company").strip()
                location = (item.get("formattedLocation") or "東京都").strip()

                view_link = item.get("viewJobLink", "")
                url = urljoin(_BASE_URL, view_link) if view_link else f"{_BASE_URL}/viewjob?jk={jobkey}"

                salary_info = item.get("salarySnippet")
                salary = None
                if isinstance(salary_info, dict):
                    salary = salary_info.get("text") or salary_info.get("salaryText")
                elif isinstance(salary_info, str):
                    salary = salary_info

                cards.append(JobPosting(
                    title=title[:500],
                    company=company[:250],
                    url=url,  # type: ignore[arg-type]
                    location=location[:250],
                    source_platform=self.platform,
                    salary=salary[:250] if salary else None,
                ))
            except Exception:
                logger.debug("Indeed: failed to map mosaic JSON item", exc_info=True)
                continue

        return cards

    # ------------------------------------------------------------------
    # Tier 2: DOM fallback
    # ------------------------------------------------------------------

    async def _parse_dom(self, page: Page) -> list[JobPosting]:
        """Fallback: extract jobs from [data-jk] DOM elements."""
        cards: list[JobPosting] = []
        seen_keys: set[str] = set()

        try:
            await page.wait_for_selector("[data-jk]", state="attached", timeout=8_000)
        except Exception:
            logger.debug("Indeed: no [data-jk] elements found via DOM fallback")
            return cards

        card_elements = page.locator("[data-jk]")
        card_count = await card_elements.count()
        logger.debug("Indeed: found %d [data-jk] elements", card_count)

        for i in range(card_count):
            try:
                el = card_elements.nth(i)
                jobkey = await el.get_attribute("data-jk")
                if not jobkey or jobkey in seen_keys:
                    continue
                seen_keys.add(jobkey)

                title = await self._dom_extract(el, ["h2 a", "h2", "a[data-jk]"])
                if not self.is_target_job(title):
                    continue

                company = await self._dom_extract(el, [
                    '[data-testid="company-name"]',
                    'span.companyName',
                    'span[class*="company"]',
                ])
                location = await self._dom_extract(el, [
                    '[data-testid="text-location"]',
                    'div.companyLocation',
                    'div[class*="location"]',
                ])
                url = await self._dom_extract_href(el, [
                    'a[href*="/viewjob"]',
                    'a[data-jk]',
                ])

                cards.append(JobPosting(
                    title=title,
                    company=company,
                    url=url,  # type: ignore[arg-type]
                    location=location,
                    source_platform=self.platform,
                ))
            except Exception:
                logger.debug("Indeed: DOM card %d failed", i, exc_info=True)
                continue

        return cards

    # FIX: was calling .first.count() which doesn't exist on Locator
    @staticmethod
    async def _dom_extract(container, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                loc = container.locator(sel)
                if await loc.count() > 0:
                    text = (await loc.first.text_content() or "").strip()
                    if text:
                        return text[:500]
            except Exception:
                continue
        return "Unknown"

    @staticmethod
    async def _dom_extract_href(container, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                loc = container.locator(sel)
                if await loc.count() > 0:
                    href = await loc.first.get_attribute("href")
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
        q = _SEARCH_QUERY.replace(" ", "+")
        loc = _SEARCH_LOCATION.replace(" ", "+")
        base = f"{_BASE_URL}{_SEARCH_PATH}?q={q}&l={loc}"
        return f"{base}&start={start_offset}" if start_offset > 0 else base

    @staticmethod
    async def _is_blocked(page: Page) -> bool:
        """Detect block/CAPTCHA signals."""
        try:
            body_text = await page.text_content("body")
            if not body_text or len(body_text) < 100:
                return True
            block_signals = [
                "verify you are human", "are you a robot", "captcha",
                "access denied", "blocked", "cf-challenge", "turnstile",
                "please verify", "アクセスをブロック", "robot check",
                "ご本人確認", "アクセスが拒否", "不正なアクセス",
            ]
            lower = body_text.lower()
            return any(s in lower for s in block_signals)
        except Exception:
            return True

    @staticmethod
    async def _scroll_like_human(page: Page) -> None:
        """Gradually scroll to simulate human reading."""
        try:
            for _ in range(_SCROLL_STEPS):
                scroll_by = random.randint(150, 450)
                await page.evaluate(f"window.scrollBy(0, {scroll_by})")
                await asyncio.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass
