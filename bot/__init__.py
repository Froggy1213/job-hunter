"""Telegram bot layer -- aiogram handlers, middlewares, and keyboards.

Handlers receive the ``Container`` DI object via middleware injection,
so they never need to import global state or use service locators.
"""

from bot.middlewares.container_middleware import ContainerMiddleware

__all__ = ["ContainerMiddleware"]
