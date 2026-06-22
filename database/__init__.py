"""Database layer -- ORM models, engine factory, and repository pattern.

The repository ABC (``JobRepository``) is the only interface that
application code depends on.  The concrete ``SQLAlchemyJobRepository``
is wired in at startup via the DI container.
"""

from database.repository import JobRepository

__all__ = ["JobRepository"]
