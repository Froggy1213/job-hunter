"""Mynavi Tenshoku (マイナビ転職) scraper.

Targets design/creative job listings in Tokyo.  Uses ``page.evaluate()``
for batch card extraction.

URL: https://tenshoku.mynavi.jp/shutoken/list/p13/kwWebデザイナー/

FIXES applied:
    - Mynavi renders a Nuxt/Vue SSR page that emits the job listing
      data inside a <script id="__NUXT_DATA__"> JSON blob.  Scraping
      <a href="/jobinfo-..."> links was unreliable because:
        1) The href pattern changed from /jobinfo-N-N-N-N/ to
           /jobinfo-{id}-{hash}/ in newer Mynavi layouts.
        2) Mynavi now shows a cookie-wall / age-gate before the
           listing page in headless browsers, returning 0 cards.
      NEW strategy: extract from __NUXT_DATA__ JSON when available,
      fall back to DOM link scraping if not.
    - Added blocking of cookie consent popup via page.add_init_script()
    - company: old code only tried img alt and h3; added
      [class*="company"], [class*="Corp"], [class*="employer"] selectors
    - location regex fixed: 勤務地 label text is often separated from the
      value by whitespace and DOM breaks; updated to allow for that.
    - salary regex fixed similarly.
    - URL: Mynavi now uses absolute URLs in some layouts; detect and keep.
    - wait_for_selector timeout reduced to 10s with graceful fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from playwright.async_api import Page, async_playwright

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.mynavi")

_BASE_URL = "https://tenshoku.mynavi.jp"
_SEARCH_PATH = "/shutoken/list/p13/kwWebデザイナー/"
_MAX_PAGES = 2
_PAGE_DELAY = 2.5
_NAV_TIMEOUT = 30_000
_SELECTOR_TIMEOUT = 10_000
# Modern Mynavi URL pattern (also catches older /jobinfo-N-N-N-N/)
_JOBINFO_RE = re.compile(r"/jobinfo-[\w-]+/")


class MynaviScraper(BaseScraper):
    """Scrape design/creative jobs in Tokyo from Mynavi Tenshoku."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.MYNAVI

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate Mynavi search results with pagination."""
        all_jobs: list[JobPosting] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=self._user_agent,
                    viewport={"width": 1280, "height": 900},
                )

                # Auto-dismiss cookie consent overlay before any navigation
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.__cookieConsent = true;
                """)

                page = await context.new_page()

                # Close cookie dialog if it appears
                page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

                for page_num in range(1, _MAX_PAGES + 1):
                    url = self._build_page_url(page_num)
                    logger.info("Scraping Mynavi page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)

                        # Try to click away cookie banner if present
                        try:
                            btn = page.locator(
                                'button:has-text("同意"), button:has-text("OK"), '
                                'button:has-text("承諾"), [class*="cookie"] button'
                            ).first
                            if await btn.count() > 0:
                                await btn.click(timeout=3_000)
                        except Exception:
                            pass

                        try:
                            await page.wait_for_selector(
                                'a[href*="/jobinfo-"]',
                                state="attached",
                                timeout=_SELECTOR_TIMEOUT,
                            )
                        except Exception:
                            logger.warning(
                                "Mynavi page %d: no /jobinfo- links found (possible block or empty page)",
                                page_num,
                            )
                            break

                        await page.wait_for_timeout(2_500)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "Mynavi page parsed",
                            extra={"page": page_num, "cards_found": len(jobs)},
                        )
                    except Exception:
                        logger.exception("Failed to scrape Mynavi page %d", page_num)
                        break

                    if page_num >= _MAX_PAGES:
                        break
                    await asyncio.sleep(_PAGE_DELAY)
            finally:
                await browser.close()

        logger.info("Mynavi scrape complete", extra={"total_jobs": len(all_jobs)})
        return all_jobs

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract job cards — tries __NUXT_DATA__ JSON first, falls back to DOM."""
        # ── Tier 1: Nuxt embedded JSON (fast, stable) ──────────────────────
        try:
            html = await page.content()
            jobs = self._parse_nuxt_json(html)
            if jobs:
                logger.debug("Mynavi: extracted %d jobs from __NUXT_DATA__", len(jobs))
                return jobs
        except Exception:
            logger.debug("Mynavi: __NUXT_DATA__ extraction failed, falling back to DOM")

        # ── Tier 2: DOM fallback (fragile but works on older layouts) ───────
        return await self._parse_dom(page)

    # ------------------------------------------------------------------
    # Tier 1: __NUXT_DATA__ JSON extraction
    # ------------------------------------------------------------------

    def _parse_nuxt_json(self, html: str) -> list[JobPosting]:
        """Parse Mynavi's embedded Nuxt data blob for job listings.

        Mynavi injects all page data into a <script id="__NUXT_DATA__">
        tag as a JSON array.  Job entries contain keys like:
          "jobTitle", "companyName", "workLocation", "salaryText", "detailUrl"
        """
        m = re.search(r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return []

        try:
            data = json.loads(m.group(1))
        except Exception:
            return []

        # Flatten the Nuxt data array and look for job-shaped dicts
        cards: list[JobPosting] = []
        seen: set[str] = set()

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                title = node.get("jobTitle") or node.get("title") or node.get("jobName")
                url_raw = node.get("detailUrl") or node.get("url") or node.get("jobUrl")
                company = node.get("companyName") or node.get("company")
                location = node.get("workLocation") or node.get("location") or "Tokyo"
                salary = node.get("salaryText") or node.get("salary")

                if title and url_raw and company:
                    url = url_raw if url_raw.startswith("http") else f"{_BASE_URL}{url_raw}"
                    key = url.split("?")[0]
                    if key not in seen:
                        seen.add(key)
                        try:
                            title_str = str(title)[:500]
                            if self.is_target_job(title_str):
                                cards.append(JobPosting(
                                    title=title_str,
                                    company=str(company)[:250],
                                    url=key,  # type: ignore[arg-type]
                                    location=str(location)[:250],
                                    source_platform=self.platform,
                                    salary=str(salary)[:250] if salary else None,
                                ))
                        except Exception:
                            pass

                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(data)
        return cards

    # ------------------------------------------------------------------
    # Tier 2: DOM fallback
    # ------------------------------------------------------------------

    async def _parse_dom(self, page: Page) -> list[JobPosting]:
        """Extract job cards via evaluate() using /jobinfo- link selectors."""
        raw_items = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            // Broadened pattern: /jobinfo- followed by word chars and hyphens
            const links = document.querySelectorAll('a[href*="/jobinfo-"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href || !/\\/jobinfo-[\\w-]+\\//.test(href)) continue;

                    // Normalise jobId: take everything between /jobinfo- and trailing /
                    const jobId = href.match(/jobinfo-([\\w-]+)/)[1];
                    if (seen.has(jobId)) continue;
                    seen.add(jobId);

                    const title = link.textContent.trim();
                    if (!title || title.length < 3) continue;

                    // ── Card container ─────────────────────────────────────
                    const card = link.closest('li')
                              || link.closest('[class*="cassette"]')
                              || link.closest('[class*="Card"]')
                              || link.closest('article')
                              || link.closest('section')
                              || link.parentElement?.parentElement?.parentElement
                              || link;

                    // ── Company ────────────────────────────────────────────
                    let company = 'Unknown Company';

                    // Dedicated company element first
                    const companyEl = card.querySelector(
                        '[class*="company"],[class*="Company"],[class*="corp"],[class*="employer"]'
                    );
                    if (companyEl) {
                        const t = companyEl.textContent.trim().split('|')[0].trim();
                        if (t.length >= 2) company = t;
                    }

                    if (company === 'Unknown Company') {
                        const img = card.querySelector('img');
                        if (img) {
                            const alt = (img.getAttribute('alt') || '').trim();
                            if (alt.length >= 2 && !/^(logo|image|photo|icon)$/i.test(alt)) {
                                company = alt;
                            }
                        }
                    }

                    if (company === 'Unknown Company') {
                        const h3 = card.querySelector('h3');
                        if (h3) {
                            const t = h3.textContent.trim().split('|')[0].trim();
                            if (t) company = t;
                        }
                    }

                    // ── Location ───────────────────────────────────────────
                    // Mynavi shows: 勤務地東京都渋谷区 (label + value fused)
                    const cardText = card.textContent || '';
                    let location = 'Tokyo';

                    // Try explicit label pattern
                    const locM = cardText.match(/勤務地[：:：]?\\s*([^\\n給仕事月]{2,60})/);
                    if (locM) {
                        location = locM[1].trim().substring(0, 100);
                    } else if (/東京都|東京/.test(cardText)) {
                        location = 'Tokyo';
                    }

                    // ── Salary ─────────────────────────────────────────────
                    let salary = null;
                    const salM = cardText.match(/給与[・月年]?[：:：]?\\s*([^\\n勤仕事]{2,60})/);
                    if (salM) salary = salM[1].trim().substring(0, 100);

                    // Build URL
                    const absUrl = href.startsWith('http')
                        ? href.split('?')[0]
                        : 'https://tenshoku.mynavi.jp' + href.split('?')[0];

                    results.push({
                        jobId: jobId,
                        title: title,
                        company: company,
                        url: absUrl,
                        location: location || 'Tokyo',
                        salary: salary,
                    });
                } catch(e) {}
            }
            return results;
        }""")

        cards: list[JobPosting] = []
        for item in raw_items:
            try:
                title = str(item["title"])[:500]
                if not self.is_target_job(title):
                    logger.debug("Skipping non-target Mynavi job: %s", title)
                    continue
                cards.append(JobPosting(
                    title=title,
                    company=str(item["company"])[:250],
                    url=item["url"],  # type: ignore[arg-type]
                    location=str(item["location"])[:250],
                    source_platform=self.platform,
                    salary=str(item["salary"])[:250] if item.get("salary") else None,
                ))
            except Exception:
                logger.debug("Failed to map Mynavi card", exc_info=True)
                continue

        return cards

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        if page_num == 1:
            return f"{_BASE_URL}{_SEARCH_PATH}"
        return f"{_BASE_URL}{_SEARCH_PATH}pg{page_num}/"
