"""/start, /stats, /subscribe, and /unsubscribe command handlers."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.utils import main_keyboard
from core.container import Container

router = Router(name="start_handler")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Welcome message with available commands and main keyboard."""
    await message.answer(
        "<b>👋 Welcome to Job Hunter Bot!</b>\n\n"
        "I collect <b>Web, UI/UX, and Graphic Design</b> jobs "
        "from Japanese job boards.\n\n"
        "Use the buttons below or type commands:\n"
        "/jobs — Browse all jobs (paginated)\n"
        "/jobs &lt;source&gt; — Filter by platform\n"
        "/stats — Job counts by platform\n"
        "/subscribe — Get notified about new jobs\n"
        "/unsubscribe — Stop notifications",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
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
    await message.answer(
        "\n".join(lines), parse_mode="HTML", reply_markup=main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Subscription commands
# ---------------------------------------------------------------------------


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, container: Container) -> None:
    """Subscribe to new-job notifications."""
    if message.from_user is None:
        return

    chat_id = message.from_user.id
    added = await container.subscriber_repository.add_subscriber(chat_id)

    if added:
        await message.answer(
            "<b>✅ Subscribed!</b>\n"
            "You will receive a notification whenever new jobs are found.",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
    else:
        await message.answer(
            "ℹ️ You are already subscribed. Use Unsubscribe button to stop notifications.",
            reply_markup=main_keyboard(),
        )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, container: Container) -> None:
    """Unsubscribe from new-job notifications."""
    if message.from_user is None:
        return

    chat_id = message.from_user.id
    removed = await container.subscriber_repository.remove_subscriber(chat_id)

    if removed:
        await message.answer(
            "<b>🔕 Unsubscribed.</b> You will no longer receive job notifications.",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
    else:
        await message.answer(
            "ℹ️ You were not subscribed.",
            reply_markup=main_keyboard(),
        )
