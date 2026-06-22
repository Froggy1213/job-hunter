"""Manual dependency injection container.

We intentionally avoid heavy DI frameworks.  All service wiring is
done explicitly in ``main.py`` -- every dependency assignment is
visible at the composition root, making the dependency graph
auditable without tooling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings
    from database.repository import JobRepository


@dataclass
class Container:
    """Holds all application-wide service references.

    Built by ``main.py`` during startup and injected into aiogram
    handlers via ``ContainerMiddleware``.  Handlers access services
    through their ``container`` parameter.

    Fields start as ``None`` and are assigned in ``main.py`` so the
    container can be instantiated before all services are ready.
    """

    settings: Settings | None = None
    """Application configuration from environment/.env."""

    repository: JobRepository | None = None
    """Job posting repository (abstract interface)."""

    logger: logging.Logger | None = field(default=None, repr=False)
    """Configured root logger for the application."""
