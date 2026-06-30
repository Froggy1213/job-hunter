"""Repository pattern interface for job posting persistence.

The ``JobRepository`` ABC abstracts the storage technology behind a
collection-like interface.  Application code works exclusively with
``JobPosting`` domain models -- never with ORM objects or raw SQL.

This is the "port" in a ports-and-adapters architecture.  The concrete
adapter (``SQLAlchemyJobRepository``) is wired in at startup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.enums import SourcePlatform
from models.job_posting import JobPosting


class JobRepository(ABC):
    """Abstract interface for persisting and querying job postings.

    All methods are async because the concrete implementation performs
    I/O (database queries).  Subclasses MUST implement every method.
    """

    @abstractmethod
    async def save(self, job: JobPosting) -> JobPosting:
        """Persist a new job posting.

        Args:
            job: The validated ``JobPosting`` to persist.

        Returns:
            The saved ``JobPosting`` (may include server-generated values
            like ``id`` if the implementation enriches it).

        Raises:
            RepositoryError: If the database operation fails (e.g. unique
                constraint violation, connection error).
        """
        ...

    @abstractmethod
    async def exists(self, url: str) -> bool:
        """Check whether a job with the given URL is already persisted.

        Args:
            url: The canonical job listing URL (string representation).

        Returns:
            ``True`` if the URL exists in the store, ``False`` otherwise.
        """
        ...

    @abstractmethod
    async def get_all(self) -> list[JobPosting]:
        """Retrieve all persisted job postings, newest first.

        Returns:
            A list of ``JobPosting`` domain models, ordered by
            ``scraped_at`` descending.  May be empty.
        """
        ...

    @abstractmethod
    async def get_by_source(self, platform: SourcePlatform) -> list[JobPosting]:
        """Retrieve job postings for a specific source platform.

        Args:
            platform: The ``SourcePlatform`` enum value to filter by.

        Returns:
            A list of ``JobPosting`` domain models for that platform,
            ordered by ``scraped_at`` descending.  May be empty.
        """
        ...

    @abstractmethod
    async def get_jobs_page(
        self,
        limit: int,
        offset: int,
        source: SourcePlatform | None = None,
    ) -> list[JobPosting]:
        """Retrieve a page of job postings.

        Args:
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip.
            source: Optional platform filter.  ``None`` means all platforms.

        Returns:
            A list of ``JobPosting`` domain models ordered by
            ``scraped_at`` descending.
        """
        ...

    @abstractmethod
    async def count_jobs(self, source: SourcePlatform | None = None) -> int:
        """Count persisted job postings.

        Args:
            source: Optional platform filter.  ``None`` means all platforms.

        Returns:
            The total number of matching job postings.
        """
        ...

    @abstractmethod
    async def get_existing_urls(self, urls: list[str]) -> set[str]:
        """Check which of the given URLs already exist in the store.

        Args:
            urls: A list of URL strings to check.

        Returns:
            The subset of *urls* that are already persisted.  May be empty.
        """
        ...
