"""Middleware that injects the DI container into every handler's ``data`` dict.

aiogram automatically unpacks ``data`` keys into handler function
parameters by name, so handlers can declare ``container: Container``
in their signature and receive it automatically -- no globals needed.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.container import Container


class ContainerMiddleware(BaseMiddleware):
    """Inject ``Container`` into the aiogram handler data dict.

    Usage in ``main.py``::

        dp.message.middleware(ContainerMiddleware(container))

    Then in any handler::

        @router.message(Command("jobs"))
        async def cmd_jobs(message: Message, container: Container) -> None:
            jobs = await container.repository.get_all()
            ...
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["container"] = self._container
        return await handler(event, data)
