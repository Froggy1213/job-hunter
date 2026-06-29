"""Concrete scraper implementations.

Each module here is a strategy that targets one specific job board.
Importing a scraper is the only step needed to make it available for
registration in ``main.py``.
"""

from scrapers.implementations.mynavi import MynaviScraper
from scrapers.implementations.mynavi2027 import Mynavi2027Scraper
from scrapers.implementations.wantedly import WantedlyScraper

from scrapers.implementations.dummy_scraper import DummyScraper  # noqa: F401 — needed by tests

__all__ = ["DummyScraper", "MynaviScraper", "Mynavi2027Scraper", "WantedlyScraper"]
