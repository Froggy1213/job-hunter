"""SQLAlchemy ORM model for the ``jobs`` table.

``JobModel`` is the persistence-side representation of ``JobPosting``.
Mapper methods in ``SQLAlchemyJobRepository`` translate between the
two, so application code never touches ORM objects directly.

The ``url`` column has a unique constraint and index -- this is the
deduplication key.  ``source_platform`` is also indexed for filtered
queries (``/jobs wantedly``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SAEnum, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from models.enums import SourcePlatform

# ---------------------------------------------------------------------------
# SQLite doesn't natively support enum CHECK constraints.  We use
# ``create_constraint=False`` for maximum portability and rely on
# Pydantic validation at the application layer instead.
# ---------------------------------------------------------------------------
_SOURCE_PLATFORM_COLUMN = SAEnum(
    SourcePlatform,
    name="source_platform_enum",
    create_constraint=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class JobModel(Base):
    """Persisted job listing.

    Each row corresponds to one unique job URL scraped from one source
    platform.  The ``url`` column is the natural deduplication key.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        unique=True,
        index=True,
        comment="Canonical URL of the job listing. Deduplication key.",
    )
    location: Mapped[str] = mapped_column(String(256), nullable=False)
    source_platform: Mapped[SourcePlatform] = mapped_column(
        _SOURCE_PLATFORM_COLUMN,
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    salary: Mapped[str | None] = mapped_column(String(256), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<JobModel id={self.id} url={self.url!r} platform={self.source_platform!r}>"


class SubscriberModel(Base):
    """Telegram chat IDs that receive new-job notifications.

    The ``chat_id`` is the Telegram chat ID (user or group).  It is the
    natural primary key -- a chat is either subscribed or it isn't.
    """

    __tablename__ = "subscribers"

    chat_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, comment="Telegram chat ID"
    )

    def __repr__(self) -> str:
        return f"<SubscriberModel chat_id={self.chat_id}>"
