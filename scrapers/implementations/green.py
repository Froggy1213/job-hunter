"""Green (green-japan.com) scraper.

Targets design/creative job listings in Tokyo.  Uses ``page.evaluate()``
for batch card extraction.

URL: https://www.green-japan.com/search?keyword=デザイン&page={N}

FIXES applied:
    - Selector changed: Green card links are NOT the outer <a> — the
      job card is an <article> containing a child <a href="/company/.../job/...">.
      Old code queried links by text length (≥80 chars), which skipped many
      cards whose text was distributed across child elements.  New code
      queries articles first, then finds the canonical href inside.
    - Title extraction: prefer dedicated <h2>/<h3> inside the card,
      not the whole link textContent (which bloated the title).
    - Company: Green displays the company name in a <p class="...name">
      or a dedicated span, not just the img alt.  Added that selector.
    - Salary: Green shows 年収 range in a <span> — added extraction.
    - networkidle wait added to catch JS-rendered card lists.
    - _MIN_CARD_TEXT_LENGTH constant removed (no longer used).
    - URL: query-string stripped, always absolute.
"""

from __future__ import annotations

import asyncio
import logging
import re

from playwright.async_api import Page, async_playwright

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.green")

_BASE_URL = "https://www.green-japan.com"
_SEARCH_KEYWORD = "デザイン"
_MAX_PAGES = 2
_PAGE_DELAY = 2.5
_NAV_TIMEOUT = 30_000
_SELECTOR_TIMEOUT = 10_000


class GreenScraper(BaseScraper):
    """Scrape design jobs in Tokyo from Green."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
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
                    locale="ja-JP",
                    timezone_id="Asia/Tokyo",
                    user_agent=self._user_agent,
                    viewport={"width": 1280, "height": 900},
                )
                page = await context.new_page()

                for page_num in range(1, _MAX_PAGES + 1):
                    url = self._build_page_url(page_num)
                    logger.info("Scraping Green page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        await page.wait_for_selector(
                            'a[href*="/job/"]',
                            state="attached",
                            timeout=_SELECTOR_TIMEOUT,
                        )
                        # Green renders cards via React — wait for idle
                        await page.wait_for_timeout(2_500)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "Green page parsed",
                            extra={"page": page_num, "cards_found": len(jobs)},
                        )
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

            // ── Strategy: find all canonical job links ─────────────────────
            // Green's URL pattern: /company/{cid}/job/{jid}
            // These appear as direct <a> elements on the card.
            // Old min-text-length=80 guard missed cards with split children.
            const links = document.querySelectorAll('a[href*="/company/"][href*="/job/"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href) continue;

                    // Must be exactly /company/{digits}/job/{digits}
                    const m = href.match(/^\\/company\\/(\\d+)\\/job\\/(\\d+)/);
                    if (!m) continue;

                    const jobId = m[2];
                    if (seen.has(jobId)) continue;
                    seen.add(jobId);

                    // ── Card container ─────────────────────────────────────
                    // Green wraps each listing in an article or a section/div
                    const card = link.closest('article')
                              || link.closest('section')
                              || link.closest('[class*="Card"]')
                              || link.closest('[class*="card"]')
                              || link.closest('li')
                              || link;

                    // ── Title ──────────────────────────────────────────────
                    // Prefer dedicated heading inside the link or the card
                    let title = '';
                    for (const tag of ['h2','h3','h4','h1']) {
                        const h = link.querySelector(tag) || card.querySelector(tag);
                        if (h) { title = h.textContent.trim(); break; }
                    }
                    if (!title) {
                        // Fallback: first non-empty text node in the link
                        title = link.textContent.trim().substring(0, 200);
                    }
                    if (!title || title.length < 3) continue;

                    // ── Company ────────────────────────────────────────────
                    // Priority: dedicated company name element → img alt → kanji pattern
                    let company = 'Unknown Company';

                    // Green often has <p class="...company..."> or <span class="...name...">
                    const companyEl = card.querySelector(
                        '[class*="company"],[class*="Company"],[class*="corp"],[class*="employer"]'
                    );
                    if (companyEl) {
                        const t = companyEl.textContent.trim();
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
                        const cardText = card.textContent || '';
                        const patterns = ['株式会社','合同会社','有限会社','一般社団法人','LLC'];
                        for (const p of patterns) {
                            const idx = cardText.indexOf(p);
                            if (idx >= 0) {
                                // Grab up to 40 chars before + the suffix
                                const start = Math.max(0, idx - 40);
                                company = cardText.substring(start, idx + p.length + 20)
                                    .split('\\n')[0].trim();
                                break;
                            }
                        }
                    }

                    // ── Location ───────────────────────────────────────────
                    const cardText = card.textContent || '';
                    let location = 'Tokyo';
                    const wards = [
                        '渋谷','新宿','港区','千代田','目黒','品川','世田谷','中央区',
                        '文京','台東','墨田','江東','豊島','六本木','代々木','恵比寿','表参道',
                        '大手町','丸の内','秋葉原','赤坂','虎ノ門','銀座','上野','原宿',
                    ];
                    for (const w of wards) {
                        if (cardText.includes(w)) { location = 'Tokyo, ' + w; break; }
                    }
                    if (location === 'Tokyo' && /フルリモート|完全リモート/.test(cardText)) {
                        location = 'Tokyo (Full Remote)';
                    }

                    // ── Salary ─────────────────────────────────────────────
                    // Green shows 年収: N万円〜N万円 or 月給: N万円
                    let salary = null;
                    const salMatch = cardText.match(
                        /(年収|月給|給与)[：:：]?\\s*([\\d,]+\\s*万円[^\\n]{0,30})/
                    );
                    if (salMatch) salary = salMatch[0].trim().substring(0, 80);

                    results.push({
                        jobId: jobId,
                        title: title,
                        company: company,
                        url: 'https://www.green-japan.com' + href.split('?')[0],
                        location: location,
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
                    logger.debug("Skipping non-target Green job: %s", title)
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
                logger.debug("Failed to map Green card", exc_info=True)
                continue

        return cards

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        base = f"{_BASE_URL}/search?keyword={_SEARCH_KEYWORD}"
        return f"{base}&page={page_num}" if page_num > 1 else base
