"""Concrete SQLAlchemy implementation of ``JobRepository``.

Each method opens and closes its own ``AsyncSession`` so there is no
shared session state between calls.  This avoids the most common class
of async SQLAlchemy bugs (lazy loads after session close, stale
identity maps, accidental cross-request state leakage).

Model ↔ Domain mapping is handled by two private static methods,
keeping the translation logic co-located and testable in isolation.
"""

from __future__ import annotations

import logging

from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.exceptions import RepositoryError
from database.models import JobModel
from database.repository import JobRepository
from models.enums import SourcePlatform
from models.job_posting import JobPosting

logger = logging.getLogger("job_hunter.repository")


class SQLAlchemyJobRepository(JobRepository):
    """Persists ``JobPosting`` domain objects via SQLAlchemy async sessions.

    Args:
        session_factory: An ``async_sessionmaker`` bound to the async engine.
            Each method calls the factory to get a fresh session.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def save(self, job: JobPosting) -> JobPosting:
        """Persist a new job posting as a new ``JobModel`` row."""
        try:
            async with self._session_factory() as session:
                model = self._domain_to_model(job)
                session.add(model)
                await session.commit()
                await session.refresh(model)
                logger.debug(
                    "Saved job",
                    extra={"url": str(job.url), "id": model.id},
                )
                return self._model_to_domain(model)
        except Exception as exc:
            raise RepositoryError(f"Failed to save job '{job.url}': {exc}") from exc

    async def exists(self, url: str) -> bool:
        """Check existence by URL (the deduplication key)."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(JobModel.id).where(JobModel.url == url).limit(1)
                )
                return result.scalar_one_or_none() is not None
        except Exception as exc:
            raise RepositoryError(f"Failed to check existence for '{url}': {exc}") from exc

    async def get_all(self) -> list[JobPosting]:
        """Return all jobs, newest first."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(JobModel).order_by(JobModel.scraped_at.desc())
                )
                return [self._model_to_domain(row) for row in result.scalars().all()]
        except Exception as exc:
            raise RepositoryError(f"Failed to fetch all jobs: {exc}") from exc

    async def get_by_source(self, platform: SourcePlatform) -> list[JobPosting]:
        """Return jobs for a specific source platform, newest first."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(JobModel)
                    .where(JobModel.source_platform == platform)
                    .order_by(JobModel.scraped_at.desc())
                )
                return [self._model_to_domain(row) for row in result.scalars().all()]
        except Exception as exc:
            raise RepositoryError(
                f"Failed to fetch jobs for source '{platform}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Model ↔ Domain mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _model_to_domain(model: JobModel) -> JobPosting:
        """Map an ORM row back to a domain ``JobPosting``."""
        return JobPosting(
            title=model.title,
            company=model.company,
            url=HttpUrl(model.url),
            location=model.location,
            source_platform=model.source_platform,
            description=model.description,
            salary=model.salary,
            posted_at=model.posted_at,
            scraped_at=model.scraped_at,
        )

    @staticmethod
    def _domain_to_model(job: JobPosting) -> JobModel:
        """Map a domain ``JobPosting`` to a new ``JobModel`` row."""
        return JobModel(
            title=job.title,
            company=job.company,
            url=str(job.url),
            location=job.location,
            source_platform=job.source_platform,
            description=job.description,
            salary=job.salary,
            posted_at=job.posted_at,
            scraped_at=job.scraped_at,
        )
