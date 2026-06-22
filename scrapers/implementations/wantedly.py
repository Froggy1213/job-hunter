"""Wantedly (ウォンテッドリー) scraper.

Targets design/creative project listings in Tokyo.  Uses ``page.evaluate()``
for batch card extraction.

URL: https://www.wantedly.com/projects?type=mixed&page=N&occupations=...&locations=tokyo
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

_BASE_URL = "https://www.wantedly.com"
_DESIGN_OCCUPATIONS = "ui_ux_designer,web_designer,graphic_designer"
_MAX_PAGES = 2
_PAGE_DELAY = 2.5
_NAV_TIMEOUT = 30_000
_PROJECT_HREF_RE = re.compile(r"^/projects/\d+")


class WantedlyScraper(BaseScraper):
    """Scrape design/creative projects in Tokyo from Wantedly."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.WANTEDLY

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate Wantedly search results with pagination."""
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
                    logger.info("Scraping Wantedly page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        await page.wait_for_selector('a[href^="/projects/"]', state="attached", timeout=15_000)
                        await page.wait_for_timeout(3_000)
                        # Scroll to trigger React lazy-load
                        await page.evaluate("window.scrollBy(0, 800)")
                        await page.wait_for_timeout(2_000)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info("Wantedly page parsed", extra={"page": page_num, "cards_found": len(jobs)})
                    except Exception:
                        logger.exception("Failed to scrape Wantedly page %d", page_num)
                        break

                    if page_num >= _MAX_PAGES:
                        break
                    await asyncio.sleep(_PAGE_DELAY)
            finally:
                await browser.close()

        logger.info("Wantedly scrape complete", extra={"total_jobs": len(all_jobs)})
        return all_jobs

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract all project cards via ``page.evaluate()``."""
        raw_items = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href^="/projects/"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href || !/^\\/projects\\/\\d+/.test(href)) continue;
                    if (href.includes('featured=0')) continue;

                    const projectId = href.match(/\\/projects\\/(\\d+)/)[1];
                    if (seen.has(projectId)) continue;
                    seen.add(projectId);

                    const linkText = link.textContent.trim();
                    if (linkText.length < 15) continue;

                    // Title: h2 or h3 inside the card link
                    let title = '';
                    for (const tag of ['h2','h3','h4']) {
                        const h = link.querySelector(tag);
                        if (h) { title = h.textContent.trim(); break; }
                    }
                    if (!title || title.length < 3) title = linkText.substring(0, 200);

                    // Company: a[href^="/companies/"] inside the card
                    let company = 'Unknown Company';
                    const companyLink = link.querySelector('a[href^="/companies/"]');
                    if (companyLink) {
                        company = companyLink.textContent.trim() || company;
                    }
                    if (company === 'Unknown Company') {
                        const img = link.querySelector('img');
                        if (img) {
                            const alt = (img.getAttribute('alt') || '').trim();
                            if (alt.length >= 2) company = alt;
                        }
                    }

                    // Location: we filter by locations=tokyo in URL, default to Tokyo
                    let location = 'Tokyo';
                    const cardText = link.textContent || '';
                    const wards = ['渋谷','新宿','港区','千代田','目黒','品川','世田谷','中央区',
                                   '文京','台東','墨田','江東','豊島','六本木','代々木','恵比寿','表参道'];
                    for (const w of wards) {
                        if (cardText.includes(w)) { location = 'Tokyo, ' + w; break; }
                    }

                    results.push({
                        projectId: projectId,
                        title: title,
                        company: company,
                        url: 'https://www.wantedly.com' + href.split('?')[0],
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
                logger.debug("Failed to map Wantedly card", exc_info=True)
                continue

        return cards

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        return (
            f"{_BASE_URL}/projects?type=mixed&page={page_num}"
            f"&occupations={_DESIGN_OCCUPATIONS}&locations=tokyo"
        )
