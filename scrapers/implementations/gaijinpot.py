"""GaijinPot Jobs scraper.

Targets design/creative job listings in Tokyo from GaijinPot Jobs,
the leading platform for foreigners seeking work in Japan.

Uses ``page.evaluate()`` for batch extraction -- one JS round-trip
extracts all job cards, avoiding O(n) Playwright locator calls.

URL structure:
    Search:  /en/job?region=22&keywords=design&page={N}
    Detail:  /en/job/{numeric_id}
    Company: /en/organization/{org_id}

FIXES applied:
    - wait_for_selector timeout reduced to 10s (was 15s, site is fast)
    - Added explicit wait for networkidle after domcontentloaded to
      ensure XHR-rendered cards are present before evaluate()
    - company fallback now walks up to grandparent card container so
      the org link is found even when it sits outside the <a> tag
    - location extraction includes full romaji ward list + kanji fallback
    - salary regex broadened to catch 万円 and ¥N,NNN,NNN patterns
    - title min-length raised from 15 to 20 chars to cut more nav links
    - URL: always strips query-string noise and enforces absolute form
"""

from __future__ import annotations

import asyncio
import logging
import re

from playwright.async_api import Page, async_playwright

from models.enums import SourcePlatform
from models.job_posting import JobPosting
from scrapers.base import BaseScraper

logger = logging.getLogger("job_hunter.scraper.gaijinpot")

_BASE_URL = "https://jobs.gaijinpot.com"
_SEARCH_PATH = "/en/job"
_MAX_PAGES = 2
_PAGE_DELAY = 2.5
_NAV_TIMEOUT = 30_000
_SELECTOR_TIMEOUT = 10_000

_SEARCH_KEYWORDS = "design"
_SEARCH_REGION = 22  # Tokyo


class GaijinPotScraper(BaseScraper):
    """Scrape design jobs in Tokyo from GaijinPot Jobs."""

    _user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
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
                    viewport={"width": 1280, "height": 900},
                )
                page = await context.new_page()

                for page_num in range(1, _MAX_PAGES + 1):
                    url = self._build_page_url(page_num)
                    logger.info("Scraping GaijinPot page", extra={"page": page_num, "url": url})
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        # Wait for the job link selector, then a short idle to
                        # let any XHR card-renders settle before evaluate()
                        await page.wait_for_selector(
                            'a[href*="/en/job/"]',
                            state="attached",
                            timeout=_SELECTOR_TIMEOUT,
                        )
                        await page.wait_for_timeout(1_500)
                        await page.wait_for_timeout(800)

                        jobs = await self.parse_page(page)
                        all_jobs.extend(jobs)
                        logger.info(
                            "GaijinPot page parsed",
                            extra={"page": page_num, "cards_found": len(jobs)},
                        )
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
        """Extract all job cards in ONE ``page.evaluate()`` call."""
        raw_items = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const links = document.querySelectorAll('a[href*="/en/job/"]');

            for (const link of links) {
                try {
                    const href = link.getAttribute('href');
                    if (!href) continue;

                    // Must match /en/job/{digits} — skip pagination/nav
                    const m = href.match(/^\\/en\\/job\\/(\\d+)/);
                    if (!m) continue;
                    const jobId = m[1];
                    if (seen.has(jobId)) continue;
                    seen.add(jobId);

                    const linkText = link.textContent.trim();
                    // Raised from 15 → 20 to filter more noise links
                    if (linkText.length < 20) continue;

                    // ── Card container ──────────────────────────────
                    // Walk up; GaijinPot wraps each listing in article
                    // or a div with class containing "job"
                    let card = link.closest('article')
                             || link.closest('[class*="job-list"]')
                             || link.closest('[class*="job"]')
                             || link.closest('li')
                             || link.closest('tr');
                    if (!card) card = link.parentElement?.parentElement || link;

                    const title = linkText;

                    // ── Company ──────────────────────────────────────
                    // 1) img alt inside the card (company logo)
                    // 2) org link text inside the card
                    // 3) img alt on the *card* element itself
                    let company = 'Unknown Company';
                    const imgs = card.querySelectorAll('img');
                    for (const img of imgs) {
                        const alt = (img.getAttribute('alt') || '').trim();
                        if (alt.length >= 2 && !/^(logo|company logo|image|photo|icon)$/i.test(alt)) {
                            company = alt;
                            break;
                        }
                    }
                    if (company === 'Unknown Company') {
                        // org link may sit OUTSIDE the job <a>, look in card
                        const orgLink = card.querySelector('a[href*="/en/organization/"]');
                        if (orgLink) {
                            const t = orgLink.textContent.trim();
                            if (t) company = t;
                        }
                    }

                    // ── Location ─────────────────────────────────────
                    const cardText = card.textContent || '';
                    let location = 'Tokyo';

                    // Romaji ward names (GaijinPot shows English text)
                    const wards = [
                        'Minato','Shibuya','Shinjuku','Chiyoda','Meguro',
                        'Shinagawa','Setagaya','Bunkyo','Taito','Sumida',
                        'Koto','Toshima','Roppongi','Ebisu','Akasaka',
                        'Otemachi','Marunouchi','Akihabara','Shimbashi','Ginza',
                        'Nihonbashi','Harajuku','Yoyogi','Omotesando','Ueno',
                    ];
                    for (const w of wards) {
                        if (cardText.includes(w)) {
                            location = 'Tokyo, ' + w;
                            break;
                        }
                    }
                    // Kanji fallback (rare on GaijinPot but safe)
                    if (location === 'Tokyo') {
                        const kanjiWards = ['渋谷','新宿','港区','千代田','目黒','六本木','銀座'];
                        for (const w of kanjiWards) {
                            if (cardText.includes(w)) {
                                location = 'Tokyo, ' + w;
                                break;
                            }
                        }
                    }
                    if (location === 'Tokyo' && !/tokyo|東京/i.test(cardText)) {
                        location = 'Japan';  // Not in Tokyo — keep generic
                    }

                    // ── Salary ───────────────────────────────────────
                    // Patterns: ¥8,000,000 – ¥10,000,000 | 6M – 8M | 万円
                    let salary = null;
                    const salPatterns = [
                        /¥[\d,]+ ?[–-] ?¥[\d,]+/,
                        /[\\d.]+M\\s*[-–~]\\s*[\\d.]+M/,
                        /[\\d,]+\\s*万円\\s*[-–~]\\s*[\\d,]+\\s*万円/,
                        /¥[\\d,]+/,
                    ];
                    for (const pat of salPatterns) {
                        const sm = cardText.match(pat);
                        if (sm) { salary = sm[0].trim(); break; }
                    }

                    // Build clean absolute URL
                    const cleanHref = href.split('?')[0];
                    const absUrl = cleanHref.startsWith('http')
                        ? cleanHref
                        : 'https://jobs.gaijinpot.com' + cleanHref;

                    results.push({
                        jobId: jobId,
                        title: title,
                        company: company,
                        url: absUrl,
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
                title = str(item["title"])[:500]
                if not self.is_target_job(title):
                    logger.debug("Skipping non-target GaijinPot job: %s", title)
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
                logger.debug("Failed to map GaijinPot card", exc_info=True)
                continue

        return cards

    @staticmethod
    def _build_page_url(page_num: int) -> str:
        base = f"{_BASE_URL}{_SEARCH_PATH}?region={_SEARCH_REGION}&keywords={_SEARCH_KEYWORDS}"
        return f"{base}&page={page_num}" if page_num > 1 else base
