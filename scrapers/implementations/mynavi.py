"""Mynavi Tenshoku (マイナビ転職) scraper.

Targets design/creative job listings in Tokyo.  Uses ``page.evaluate()``
for batch card extraction.

URL: https://tenshoku.mynavi.jp/shutoken/list/p13/o1A/pg{N}/
     shutoken=首都圏, p13=東京都, o1A=クリエイティブ職種
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

logger = logging.getLogger("job_hunter.scraper.mynavi")

_BASE_URL = "https://tenshoku.mynavi.jp"
_SEARCH_PATH = "/shutoken/list/p13/kwWebデザイナー/"
_MAX_PAGES = 2
_PAGE_DELAY = 2.0
_NAV_TIMEOUT = 30_000
_JOBINFO_RE = re.compile(r"/jobinfo-\d+-\d+-\d+-\d+/")


class MynaviScraper(BaseScraper):
    """Scrape design/creative jobs in Tokyo from Mynavi Tenshoku."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
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
                    locale="ja-JP", timezone_id="Asia/Tokyo", user_agent=self._user_agent,
                )
                page = await context.new_page()

                for page_num in range(1, _MAX_PAGES + 1):
                    url = self._build_page_url(page_num)
                    logger.info("Scraping Mynavi page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        await page.wait_for_selector('a[href*="/jobinfo-"]', state="attached", timeout=15_000)
                        await page.wait_for_timeout(2_000)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info("Mynavi page parsed", extra={"page": page_num, "cards_found": len(jobs)})
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
        """Extract all job cards via ``page.evaluate()``."""
        raw_items = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/jobinfo-"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href || !/\\/jobinfo-\\d+-\\d+-\\d+-\\d+\\//.test(href)) continue;

                    const jobId = href.match(/jobinfo-(\\d+-\\d+-\\d+-\\d+)/)[1];
                    if (seen.has(jobId)) continue;
                    seen.add(jobId);

                    const title = link.textContent.trim();
                    if (!title || title.length < 3) continue;

                    // Card container
                    const card = link.closest('li') || link.closest('[class*="cassette"]')
                              || link.closest('article') || link.closest('section')
                              || link.parentElement?.parentElement?.parentElement || link;

                    // Company: img alt, then h3
                    let company = 'Unknown Company';
                    const img = card.querySelector('img');
                    if (img) {
                        const alt = (img.getAttribute('alt') || '').trim();
                        if (alt.length >= 2) company = alt;
                    }
                    if (company === 'Unknown Company') {
                        const h3 = card.querySelector('h3');
                        if (h3) {
                            const t = h3.textContent.trim().split('|')[0].trim();
                            if (t) company = t;
                        }
                    }

                    // Location
                    const cardText = card.textContent || '';
                    let location = 'Tokyo';
                    const locMatch = cardText.match(/勤務地[：:]\\s*(.+?)(?:\\n|給与|仕事内容|$)/);
                    if (locMatch) location = locMatch[1].trim().substring(0, 100);

                    // Salary
                    let salary = null;
                    const salMatch = cardText.match(/給与[：:]\\s*(.+?)(?:\\n|勤務地|仕事内容|$)/);
                    if (salMatch) salary = salMatch[1].trim().substring(0, 100);

                    results.push({
                        jobId: jobId,
                        title: title,
                        company: company,
                        url: 'https://tenshoku.mynavi.jp' + href,
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
