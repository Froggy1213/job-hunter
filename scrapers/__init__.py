"""Scraper layer -- the Strategy pattern for job board scrapers.

``BaseScraper`` is the abstract strategy that every job board must
implement.  ``ScraperOrchestrator`` runs all registered scrapers
concurrently and handles deduplication.

To add a new job board:
    1. Add the platform to ``models.enums.SourcePlatform``.
    2. Subclass ``BaseScraper`` in ``scrapers/implementations/``.
    3. Register the instance in the scraper list in ``main.py``.
"""

from scrapers.base import BaseScraper
from scrapers.orchestrator import ScraperOrchestrator

__all__ = ["BaseScraper", "ScraperOrchestrator"]
