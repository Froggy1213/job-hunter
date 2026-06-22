"""Core infrastructure -- exceptions, DI container, and logging setup.

This package contains no business logic.  It provides the plumbing
that the domain, database, scraper, and bot layers all depend on.
"""

from core.container import Container
from core.exceptions import ConfigurationError, JobHunterError, RepositoryError, ScraperError
from core.logging_setup import setup_logging

__all__ = [
    "Container",
    "ConfigurationError",
    "JobHunterError",
    "RepositoryError",
    "ScraperError",
    "setup_logging",
]
