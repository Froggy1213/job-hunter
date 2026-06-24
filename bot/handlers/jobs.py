"""/jobs command handler with pagination.

Shows 5 job postings per page with ⬅️ ➡️ inline navigation buttons.
Supports optional source-platform filtering via /jobs &lt;source&gt;.
"""

from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.utils import escape, job_card
from core.container import Container
from models.enums import SourcePlatform

logger = logging.getLogger("job_hunter.bot")
router = Router(name="jobs_handler")

_JOBS_PER_PAGE = 5

# ---------------------------------------------------------------------------
# /jobs command
# ---------------------------------------------------------------------------


@router.message(Command("jobs"))
async def cmd_jobs(
    message: Message,
    command: CommandObject,
    container: Container,
) -> None:
    """Handle /jobs [source] — show paginated job listings."""
    source = _parse_source(command.args)
    if source is False:
        # Invalid source name -- error already sent by _parse_source.
        valid = ", ".join(p.value for p in SourcePlatform if p != SourcePlatform.DUMMY)
        await message.answer(
            f"❓ Unknown source. Available: {valid}", parse_mode="HTML",
        )
        return

    await _show_page(message, container, page=0, source=source)


# ---------------------------------------------------------------------------
# Inline keyboard callback (pagination)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("jobs:"))
async def on_jobs_page(callback: CallbackQuery, container: Container) -> None:
    """Handle ⬅️ ➡️ pagination button presses.

    Callback data format: ``jobs:{page}:{source}``
    - ``page``: 0-based page index
    - ``source``: ``"all"`` or a SourcePlatform value
    """
    _, page_str, source_str = callback.data.split(":", 2)
    page = int(page_str)
    source = None if source_str == "all" else SourcePlatform(source_str)

    await _show_page(callback.message, container, page=page, source=source, edit=True)
    await callback.answer()


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


async def _show_page(
    msg: Message,
    container: Container,
    *,
    page: int = 0,
    source: SourcePlatform | None = None,
    edit: bool = False,
) -> None:
    """Fetch jobs and render one page with navigation buttons.

    Args:
        msg: The Message to reply to or edit.
        container: DI container.
        page: 0-based page index.
        source: Optional platform filter.
        edit: If True, edit *msg* in-place; otherwise send a new message.
    """
    # Fetch.
    if source is not None:
        all_jobs = await container.repository.get_by_source(source)
    else:
        all_jobs = await container.repository.get_all()

    total_pages = max(1, (len(all_jobs) + _JOBS_PER_PAGE - 1) // _JOBS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * _JOBS_PER_PAGE
    page_jobs = all_jobs[start:start + _JOBS_PER_PAGE]

    # Build text.
    source_label = f"<code>{source.value}</code>" if source else "all"
    lines = [
        f"<b>📋 Jobs ({len(all_jobs)} total, {source_label})</b>",
        f"Page {page + 1}/{total_pages}\n",
    ]

    if not page_jobs:
        lines.append("No jobs found. They will appear after the next scrape.")
    else:
        for i, job in enumerate(page_jobs, start=start + 1):
            lines.append(job_card(job, i))

    text = "\n".join(lines)

    # Build inline keyboard.
    keyboard = _build_nav_keyboard(page, total_pages, source)

    if edit:
        await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await msg.answer(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_source(args: str | None) -> SourcePlatform | None | bool:
    """Parse an optional source argument.

    Returns:
        A ``SourcePlatform``, ``None`` (no filter), or ``False`` (invalid).
    """
    if not args or not args.strip():
        return None
    source_str = args.strip().lower()
    # Hide DUMMY from users.
    if source_str == "dummy":
        return False
    try:
        return SourcePlatform(source_str)
    except ValueError:
        return False


def _build_nav_keyboard(
    page: int,
    total_pages: int,
    source: SourcePlatform | None,
) -> InlineKeyboardMarkup | None:
    """Build ⬅️ ➡️ navigation buttons, or None if only one page."""
    if total_pages <= 1:
        return None

    source_key = source.value if source else "all"
    buttons: list[InlineKeyboardButton] = []

    if page > 0:
        buttons.append(InlineKeyboardButton(
            text="⬅️ Prev",
            callback_data=f"jobs:{page - 1}:{source_key}",
        ))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton(
            text="Next ➡️",
            callback_data=f"jobs:{page + 1}:{source_key}",
        ))

    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
