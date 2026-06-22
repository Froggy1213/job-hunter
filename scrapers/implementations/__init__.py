"""Concrete scraper implementations.

Each module here is a strategy that targets one specific job board.
Importing a scraper is the only step needed to make it available for
registration in ``main.py``.
"""

from scrapers.implementations.dummy_scraper import DummyScraper
from scrapers.implementations.gaijinpot import GaijinPotScraper
from scrapers.implementations.green import GreenScraper
from scrapers.implementations.indeed import IndeedScraper
from scrapers.implementations.mynavi import MynaviScraper
from scrapers.implementations.wantedly import WantedlyScraper

__all__ = [
    "DummyScraper",
    "GaijinPotScraper",
    "GreenScraper",
    "IndeedScraper",
    "MynaviScraper",
    "WantedlyScraper",
]
