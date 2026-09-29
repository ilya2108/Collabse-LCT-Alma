"""Middleware бота: rate-limit команд по chat_id (KeyDB crm:v1:rl:tg:{chat_id})
и учёт обработанных команд в метриках (GET /api/v1/stats)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.bot.texts import RATE_LIMITED_TEXT
from app.metrics import Metrics
from app.ratelimit import TelegramRateLimiter


def _command_name(message: Message) -> str | None:
    text = message.text or ""
    if not text.startswith("/"):
        return None
    command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0]
    return command.lstrip("/").lower() or None


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, limiter: TelegramRateLimiter, metrics: Metrics | None = None) -> None:
        self._limiter = limiter
        self._metrics = metrics

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if not await self._limiter.allow(event.chat.id):
                if self._metrics is not None:
                    self._metrics.mark_bot_rate_limited()
                await event.answer(RATE_LIMITED_TEXT)
                return None
            if self._metrics is not None:
                command = _command_name(event)
                if command is not None:
                    self._metrics.mark_bot_command(command)
        return await handler(event, data)
