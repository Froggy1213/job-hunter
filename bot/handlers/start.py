"""/start and /stats command handlers."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.container import Container

router = Router(name="start_handler")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Welcome message with available commands."""
    await message.answer(
        "<b>👋 Welcome to Job Hunter Bot!</b>\n\n"
        "I collect <b>Web, UI/UX, and Graphic Design</b> jobs "
        "from Japanese job boards.\n\n"
        "<b>Commands:</b>\n"
        "/jobs — Browse all jobs (paginated)\n"
        "/jobs &lt;source&gt; — Filter by platform\n"
        "/stats — Job counts by platform\n"
        "/start — This message",
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, container: Container) -> None:
    """Show job counts per platform."""
    from models.enums import SourcePlatform

    lines = ["<b>📊 Job Statistics</b>\n"]
    total = 0

    for platform in SourcePlatform:
        if platform == SourcePlatform.DUMMY:
            continue
        jobs = await container.repository.get_by_source(platform)
        count = len(jobs)
        total += count
        lines.append(f"• <code>{platform.value}</code>: {count}")

    lines.append(f"\n<b>Total: {total}</b>")
    await message.answer("\n".join(lines), parse_mode="HTML")
