"""Services package — application-level business logic.

Services sit between the composition root (``main.py``) and the
infrastructure layer (scrapers, database, bot).  They encapsulate
multi-step workflows that involve several dependencies working together.
"""

from services.notifier import ScrapeNotifierService

__all__ = ["ScrapeNotifierService"]
