"""/start command handler.

The entry point for users -- shows available commands and a brief
description of what the bot does.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="start_handler")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Handle the /start command.

    Sends a welcome message with the list of available commands.
    Does not require any injected dependencies.
    """
    await message.answer(
        "👋 Welcome to Job Hunter Bot!\n\n"
        "I aggregate design job listings from Japanese job boards.\n\n"
        "Available commands:\n"
        "/jobs — List all collected jobs\n"
        "/jobs <source> — Filter by source (e.g. /jobs dummy)\n"
        "/start — Show this message",
    )
