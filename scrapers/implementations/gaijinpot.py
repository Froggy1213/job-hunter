"""GaijinPot Jobs scraper.

Targets design/creative job listings in Tokyo from GaijinPot Jobs,
the leading platform for foreigners seeking work in Japan.

Uses ``page.evaluate()`` for batch extraction -- one JS round-trip
extracts all job cards, avoiding O(n) Playwright locator calls.

URL structure:
    Search:  /en/job?region=22&keywords=design&page={N}
    Detail:  /en/job/{numeric_id}
    Company: /en/organization/{org_id}
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

_BASE_URL = "https://jobs.gaijinpot.com"
_SEARCH_PATH = "/en/job"
_MAX_PAGES = 2
_PAGE_DELAY = 2.0
_NAV_TIMEOUT = 30_000

_JOB_HREF_RE = re.compile(r"^/en/job/\d+")
_SEARCH_KEYWORDS = "design"
_SEARCH_REGION = 22


class GaijinPotScraper(BaseScraper):
    """Scrape design jobs in Tokyo from GaijinPot Jobs."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def platform(self) -> SourcePlatform:
        return SourcePlatform.GAIJINPOT

    async def fetch_jobs(self) -> list[JobPosting]:
        """Navigate GaijinPot search results with pagination."""
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
                    logger.info("Scraping GaijinPot page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        await page.wait_for_selector('a[href*="/en/job/"]', state="attached", timeout=15_000)
                        await page.wait_for_timeout(1_500)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info("GaijinPot page parsed", extra={"page": page_num, "cards_found": len(jobs)})
                    except Exception:
                        logger.exception("Failed to scrape GaijinPot page %d", page_num)
                        break

                    if page_num >= _MAX_PAGES:
                        break
                    await asyncio.sleep(_PAGE_DELAY)

            finally:
                await browser.close()

        logger.info("GaijinPot scrape complete", extra={"total_jobs": len(all_jobs)})
        return all_jobs

    async def parse_page(self, page: Page) -> list[JobPosting]:
        """Extract all job cards in ONE ``page.evaluate()`` call.

        Returns validated ``JobPosting`` domain models.
        """
        raw_items = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/en/job/"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href) continue;

                    // Must match /en/job/{digits}
                    const m = href.match(/^\\/en\\/job\\/(\\d+)/);
                    if (!m) continue;
                    const jobId = m[1];
                    if (seen.has(jobId)) continue;
                    seen.add(jobId);

                    // Filter thin links (pagination, nav)
                    const linkText = link.textContent.trim();
                    if (linkText.length < 15) continue;

                    // Resolve card container
                    let card = link.closest('article')
                            || link.closest('tr')
                            || link.closest('li')
                            || link.closest('[class*="job"]')
                            || link.parentElement?.parentElement
                            || link;

                    // Title
                    const title = linkText;

                    // Company: img alt first, then org link
                    let company = 'Unknown Company';
                    const imgs = card.querySelectorAll('img');
                    for (const img of imgs) {
                        const alt = (img.getAttribute('alt') || '').trim();
                        if (alt.length >= 2 && !/^(logo|company logo|image)$/i.test(alt)) {
                            company = alt;
                            break;
                        }
                    }
                    if (company === 'Unknown Company') {
                        const orgLink = card.querySelector('a[href*="/en/organization/"]');
                        if (orgLink) company = orgLink.textContent.trim() || company;
                    }

                    // Location
                    const cardText = card.textContent || '';
                    let location = 'Japan';
                    const wards = ['Minato','Shibuya','Shinjuku','Chiyoda','Meguro',
                                   'Shinagawa','Setagaya','Bunkyo','Taito','Sumida',
                                   'Koto','Toshima','Roppongi','Ebisu','Akasaka',
                                   'Otemachi','Marunouchi','Akihabara','Shimbashi','Ginza'];
                    for (const w of wards) {
                        if (cardText.toLowerCase().includes(w.toLowerCase())) {
                            location = 'Tokyo, ' + w;
                            break;
                        }
                    }
                    if (location === 'Japan' && /tokyo|東京都|東京/i.test(cardText)) {
                        location = 'Tokyo';
                    }

                    // Salary
                    let salary = null;
                    const yenIdx = cardText.indexOf('¥');
                    if (yenIdx >= 0) {
                        salary = cardText.substring(Math.max(0, yenIdx - 5), yenIdx + 40).split(/\\n|  | \\/ |Location|Company/)[0].trim();
                        if (salary.length > 60) salary = null;
                    }
                    if (!salary) {
                        const rangeM = cardText.match(/[\\d.]+M\\s*[-–~]\\s*[\\d.]+M/);
                        if (rangeM) salary = rangeM[0];
                    }

                    results.push({
                        jobId: jobId,
                        title: title,
                        company: company,
                        url: href.startsWith('http') ? href : 'https://jobs.gaijinpot.com' + href,
                        location: location,
                        salary: salary,
                    });
                } catch(e) { /* skip malformed card */ }
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
                    salary=str(item["salary"])[:250] if item.get("salary") else None,
                ))
            except Exception:
                logger.debug("Failed to map GaijinPot card", exc_info=True)
                continue

        return cards

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        base = f"{_BASE_URL}{_SEARCH_PATH}?region={_SEARCH_REGION}&keywords={_SEARCH_KEYWORDS}"
        return f"{base}&page={page_num}" if page_num > 1 else base
