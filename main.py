"""Job Hunter Bot -- composition root.

This is the entry point and dependency-injection composition root.
Every service is instantiated and wired here explicitly -- there is
no auto-discovery, no magic, and no global mutable state.  The
dependency graph is fully visible in this single file.

Architecture layers (inner depends on outer? no -- concentric):

::

    ┌──────────────────────────────────────────────┐
    │                    main.py                    │  ← composition root
    ├──────────────────────────────────────────────┤
    │  bot/           scrapers/       database/     │  ← application layers
    │  (aiogram)      (playwright)   (sqlalchemy)   │
    ├──────────────────────────────────────────────┤
    │  core/          config/         models/       │  ← infrastructure
    └──────────────────────────────────────────────┘

Adding a new job board requires exactly three changes:
    1. Write the scraper in ``scrapers/implementations/``.
    2. Add the enum member to ``models/enums.py``.
    3. Register the instance in the ``scrapers`` list below.

No other file is touched.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.handlers import jobs as jobs_handler
from bot.handlers import start as start_handler
from bot.middlewares.container_middleware import ContainerMiddleware
from config.settings import Settings
from core.container import Container
from core.logging_setup import setup_logging
from database.engine import create_engine_and_session
from database.models import Base
from database.sqlalchemy_repository import SQLAlchemyJobRepository, SQLAlchemySubscriberRepository
from scrapers.implementations.dummy_scraper import DummyScraper
from scrapers.implementations.mynavi import MynaviScraper
from scrapers.implementations.gaijinpot import GaijinPotScraper
from scrapers.implementations.green import GreenScraper
from scrapers.implementations.wantedly import WantedlyScraper
from scrapers.orchestrator import ScraperOrchestrator

logger = logging.getLogger("job_hunter")


async def main() -> None:
    """Bootstrap and run the bot.

    Startup order:
        1. Load & validate configuration (.env)
        2. Configure structured logging
        3. Create database engine + tables
        4. Instantiate repository (SQLAlchemy adapter)
        5. Register scrapers (Strategy pattern)
        6. Create orchestrator
        7. Build DI container
        8. Create bot + dispatcher + middleware + routers
        9. Initialise scheduler with periodic scrape job
       10. Start polling
    """
    # ---- 1. Configuration (validates .env at import time) ----
    settings = Settings()

    # ---- 2. Structured logging ----
    setup_logging(settings.log_level)
    logger.info(
        "Starting Job Hunter bot",
        extra={
            "database_url": settings.database_url,
            "headless": settings.playwright_headless,
            "admin_chat_id": settings.admin_chat_id,
        },
    )

    # ---- 3. Database ----
    engine, session_factory = create_engine_and_session(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")

    repository = SQLAlchemyJobRepository(session_factory)
    subscriber_repo = SQLAlchemySubscriberRepository(session_factory)

    # ---- 4. Scrapers (Strategy pattern -- add new boards here) ----
    scrapers = [
        DummyScraper(headless=settings.playwright_headless),
        MynaviScraper(headless=settings.playwright_headless),
        WantedlyScraper(headless=settings.playwright_headless),
        GreenScraper(headless=settings.playwright_headless),
        GaijinPotScraper(headless=settings.playwright_headless),
        # IndeedScraper(headless=settings.playwright_headless),  # Отключен: Cloudflare блок
    ]
    orchestrator = ScraperOrchestrator(scrapers, repository)
    logger.info(
        "Scrapers registered",
        extra={"platforms": [p.value for p in orchestrator.platforms]},
    )

    # ---- 5. DI Container ----
    container = Container()
    container.settings = settings
    container.repository = repository
    container.orchestrator = orchestrator
    container.subscriber_repository = subscriber_repo
    container.logger = logger

    # ---- 6. Bot & Dispatcher ----
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware -- injects Container into every handler's data dict
    dp.message.middleware(ContainerMiddleware(container))
    dp.callback_query.middleware(ContainerMiddleware(container))

    # Routers -- each module exports a standalone aiogram.Router
    dp.include_router(start_handler.router)
    dp.include_router(jobs_handler.router)

    # ---- 7. Scheduled scraping ----
    scheduler = AsyncIOScheduler()

    def _format_broadcast_card(job) -> str:
        """Format a job posting for broadcast (no index number)."""
        from html import escape as _esc

        title = _esc(job.title)
        company = _esc(job.company)
        location = _esc(job.location)
        salary = _esc(job.salary or "—")
        url = _esc(str(job.url))
        source = _esc(job.source_platform.value)

        return (
            f"<b><a href=\"{url}\">{title}</a></b>\n"
            f"🏢 {company}\n"
            f"📍 {location}  |  💰 {salary}\n"
            f"📡 <code>{source}</code>"
        )

    async def run_scheduled_scrape() -> None:
        """Execute all scrapers and notify subscribers about new jobs.

        Defined as a closure so it has access to ``orchestrator``,
        ``bot``, ``subscriber_repo``, and ``settings`` without threading
        them through globals or the DI container.  APScheduler calls this
        on a timer.
        """
        logger.info("Scheduled scrape triggered")
        try:
            result = await orchestrator.run_all()
        except Exception:
            logger.exception("Scheduled scrape failed")
            return

        total_new = sum(result.counts.values())
        if total_new > 0:
            # ---- Admin summary ----
            platform_lines = [
                f"  • <code>{platform.value}</code>: {count} new"
                for platform, count in result.counts.items()
                if count > 0
            ]
            summary = "\n".join(platform_lines)

            await bot.send_message(
                chat_id=settings.admin_chat_id,
                text=(
                    f"<b>🔔 Scrape complete!</b>\n"
                    f"Found <b>{total_new}</b> new job listing(s):\n"
                    f"{summary}\n\n"
                    f"Use /jobs to view them."
                ),
                parse_mode="HTML",
            )

            # ---- Broadcast new jobs to subscribers ----
            subscribers = await subscriber_repo.get_all_subscribers()
            if subscribers and result.new_jobs:
                logger.info(
                    "Broadcasting new jobs to subscribers",
                    extra={"subscribers": len(subscribers), "jobs": len(result.new_jobs)},
                )
                for job in result.new_jobs:
                    card_no_index = _format_broadcast_card(job)
                    for chat_id in subscribers:
                        try:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=f"<b>🆕 New Job!</b>\n{card_no_index}",
                                parse_mode="HTML",
                            )
                            await asyncio.sleep(0.05)  # 20 msg/s rate limit safety
                        except Exception:
                            logger.debug(
                                "Failed to notify subscriber",
                                extra={"chat_id": chat_id},
                                exc_info=True,
                            )

            logger.info(
                "Scheduled scrape notification sent",
                extra={"new_jobs": total_new, "subscribers_notified": len(subscribers)},
            )
        else:
            logger.info("Scheduled scrape found no new jobs")

    scheduler.add_job(
        run_scheduled_scrape,
        trigger=IntervalTrigger(hours=4),
        id="scheduled_scrape",
        name="Periodic job board scrape",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (interval: 4 hours)")

    # ---- 8. Polling ----
    logger.info("Bot starting polling")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down")
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
