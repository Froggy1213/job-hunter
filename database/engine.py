"""Async SQLAlchemy engine and session factory.

Uses ``NullPool`` by default, which is required for SQLite (it doesn't
support connection pooling across threads/asyncio tasks).  Switch to
a pooled configuration when using PostgreSQL with ``asyncpg``.

Architecture note: the session factory is passed to the repository,
NOT the engine.  This lets each repository method own its session
lifecycle, avoiding shared-session bugs.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_engine_and_session(
    url: str,
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build an async engine and session factory.

    Args:
        url: SQLAlchemy async database URL (e.g.
            ``"sqlite+aiosqlite:///./jobs.db"`` or
            ``"postgresql+asyncpg://user:pass@localhost/db"``).
        echo: If ``True``, log every SQL statement (verbose; useful
            during development).

    Returns:
        A ``(engine, session_factory)`` tuple.  The engine is used for
        schema creation and graceful shutdown; the session factory is
        injected into the repository.
    """
    engine = create_async_engine(url, echo=echo, poolclass=NullPool)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, session_factory
