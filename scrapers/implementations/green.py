"""Green (green-japan.com) scraper.

Targets design/creative job listings in Tokyo.  Uses ``page.evaluate()``
for batch card extraction.

URL: https://www.green-japan.com/search?keyword=デザイン&page={N}
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

_BASE_URL = "https://www.green-japan.com"
_SEARCH_KEYWORD = "デザイン"
_MAX_PAGES = 2
_PAGE_DELAY = 2.0
_NAV_TIMEOUT = 30_000
_JOB_HREF_RE = re.compile(r"^/company/\d+/job/\d+")
_MIN_CARD_TEXT_LENGTH = 80


class GreenScraper(BaseScraper):
    """Scrape design jobs in Tokyo from Green."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.GREEN

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate Green search results with pagination."""
        all_jobs: list[JobPosting] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            try:
                context = await browser.new_context(
                    locale="ja-JP", timezone_id="Asia/Tokyo", user_agent=self._user_agent,
                )
                page = await context.new_page()

                for page_num in range(1, _MAX_PAGES + 1):
                    url = self._build_page_url(page_num)
                    logger.info("Scraping Green page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        await page.wait_for_selector('a[href*="/job/"]', state="attached", timeout=15_000)
                        await page.wait_for_timeout(2_000)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info("Green page parsed", extra={"page": page_num, "cards_found": len(jobs)})
                    except Exception:
                        logger.exception("Failed to scrape Green page %d", page_num)
                        break

                    if page_num >= _MAX_PAGES:
                        break
                    await asyncio.sleep(_PAGE_DELAY)
            finally:
                await browser.close()

        logger.info("Green scrape complete", extra={"total_jobs": len(all_jobs)})
        return all_jobs

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract all job cards via ``page.evaluate()``."""
        raw_items = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/job/"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href || !/^\\/company\\/\\d+\\/job\\/\\d+/.test(href)) continue;

                    const text = link.textContent.trim();
                    if (text.length < 80) continue;  // skip thin "他のN求人" links

                    const jobId = href.match(/\\/job\\/(\\d+)/)[1];
                    if (seen.has(jobId)) continue;
                    seen.add(jobId);

                    // Title: h2 or h3
                    let title = 'Unknown Title';
                    for (const tag of ['h2','h3','h4']) {
                        const h = link.querySelector(tag);
                        if (h) { title = h.textContent.trim(); break; }
                    }
                    if (title === 'Unknown Title') title = text.substring(0, 200);

                    // Company: img alt or 株式会社 pattern
                    let company = 'Unknown Company';
                    const img = link.querySelector('img');
                    if (img) {
                        const alt = (img.getAttribute('alt') || '').trim();
                        if (alt.length >= 2) company = alt;
                    }
                    if (company === 'Unknown Company') {
                        const patterns = ['株式会社','合同会社','有限会社','一般社団法人'];
                        for (const p of patterns) {
                            const idx = text.indexOf(p);
                            if (idx >= 0) {
                                const start = Math.max(0, idx - 60);
                                company = text.substring(start, idx + p.length).replace(/\\n/g,'').trim();
                                break;
                            }
                        }
                    }

                    // Location
                    let location = 'Tokyo';
                    const wards = ['渋谷','新宿','港区','千代田','目黒','品川','世田谷','中央区',
                                   '文京','台東','墨田','江東','豊島','六本木','代々木','恵比寿','表参道',
                                   '大手町','丸の内','秋葉原','赤坂','虎ノ門'];
                    for (const w of wards) {
                        if (text.includes(w)) { location = 'Tokyo, ' + w; break; }
                    }
                    if (location === 'Tokyo' && /フルリモート/.test(text)) location = 'Tokyo (Full Remote)';

                    results.push({
                        jobId: jobId,
                        title: title,
                        company: company,
                        url: 'https://www.green-japan.com' + href.split('?')[0],
                        location: location,
                        salary: null,
                    });
                } catch(e) {}
            }
            return results;
        }""")

        cards: list[JobPosting] = []
        for item in raw_items:
            try:
                cards.append(JobPosting(
                    title=str(item["title"])[:500],
                    company=str(item["company"])[:250],
                    url=item["url"],  # type: ignore[arg-type]
                    location=str(item["location"])[:250],
                    source_platform=self.platform,
                ))
            except Exception:
                logger.debug("Failed to map Green card", exc_info=True)
                continue

        return cards

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        base = f"{_BASE_URL}/search?keyword={_SEARCH_KEYWORD}"
        return f"{base}&page={page_num}" if page_num > 1 else base
