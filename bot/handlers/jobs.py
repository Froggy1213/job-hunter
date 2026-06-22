"""/jobs command handler.

Lists job postings from the repository, with an optional source-platform
filter parsed from the command arguments.

Examples:
    /jobs          → all jobs, newest first
    /jobs dummy    → only dummy-platform jobs
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from core.container import Container
from models.enums import SourcePlatform

logger = logging.getLogger("job_hunter.bot")
router = Router(name="jobs_handler")

# Limit the number of jobs shown in a single message to avoid
# hitting Telegram's message-length limit (4096 characters).
_MAX_JOBS_PER_MESSAGE = 10


@router.message(Command("jobs"))
async def cmd_jobs(
    message: Message,
    command: CommandObject,
    container: Container,
) -> None:
    """Handle the /jobs command with an optional source-platform filter.

    The ``container`` parameter is injected by ``ContainerMiddleware``.
    ``command`` is provided by aiogram's built-in ``CommandObject`` filter
    and gives access to the raw argument string.
    """
    # ---- Parse optional source filter ----
    source_str = command.args.strip().lower() if command.args else None
    source: SourcePlatform | None = None

    if source_str:
        try:
            source = SourcePlatform(source_str)
        except ValueError:
            valid = ", ".join(p.value for p in SourcePlatform)
            await message.answer(
                f"❓ Unknown source: `{source_str}`\n"
                f"Available sources: {valid}",
            )
            return

    # ---- Fetch from repository ----
    if source is not None:
        jobs = await container.repository.get_by_source(source)
    else:
        jobs = await container.repository.get_all()

    if not jobs:
        msg = "📭 No job listings found."
        if source is None:
            msg += "\n\nThere are no scraped jobs yet.  Ask an admin to run a scrape."
        else:
            msg += f"\n\nNo jobs from source `{source.value}`.  Try a different filter."
        await message.answer(msg)
        return

    # ---- Format output ----
    lines: list[str] = [f"*📋 Job Listings*  \\({len(jobs)} total\\)\n"]
    for i, job in enumerate(jobs[:_MAX_JOBS_PER_MESSAGE], start=1):
        salary = job.salary or "Not specified"
        lines.append(
            f"{i}\\. [{_escape_md(job.title)}]({job.url})\n"
            f"  🏢 {_escape_md(job.company)}\n"
            f"  📍 {_escape_md(job.location)}\n"
            f"  💰 {_escape_md(salary)}\n"
            f"  📡 `{job.source_platform.value}`"
        )

    text = "\n\n".join(lines)

    if len(jobs) > _MAX_JOBS_PER_MESSAGE:
        text += (
            f"\n\n_Showing {_MAX_JOBS_PER_MESSAGE} "
            f"of {len(jobs)} results._"
        )

    await message.answer(
        text,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )


def _escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters in *text*.

    aiogram's ``MarkdownV2`` parse mode requires escaping::
        _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    specials = r"_*[]()~`>#+-=|{}.!"
    for ch in specials:
        text = text.replace(ch, f"\\{ch}")
    return text
