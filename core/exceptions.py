"""Domain-level exception hierarchy.

A dedicated base exception (``JobHunterError``) lets callers catch
all application errors in a single ``except`` clause.  Specific
subclasses carry semantic meaning about which layer failed.
"""


class JobHunterError(Exception):
    """Base exception for every application-level error.

    Catching ``JobHunterError`` handles all expected failures
    regardless of which layer raised them.
    """


class ScraperError(JobHunterError):
    """Raised when a scraper cannot fetch or parse job data.

    The ``__cause__`` chain preserves the original exception
    (e.g. a Playwright timeout or HTTP error) for debugging.
    """


class RepositoryError(JobHunterError):
    """Raised when a database operation fails.

    Typically wraps a SQLAlchemy exception as its ``__cause__``.
    """


class ConfigurationError(JobHunterError):
    """Raised when application configuration is invalid.

    Caught at startup before the bot begins polling, so the process
    fails fast rather than running with broken config.
    """
