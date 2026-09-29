"""Rate-limit команд Telegram-бота: KeyDB crm:v1:rl:tg:{chat_id} (data-model.md §10).

INCR + EXPIRE 60 секунд; при недоступности KeyDB лимит не применяется (деградация
в сторону доступности — бот важнее лимита на демо-стенде).
"""
from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_KEY_TEMPLATE = "crm:v1:rl:tg:{chat_id}"
_WINDOW_SECONDS = 60


class TelegramRateLimiter:
    def __init__(self, keydb_url: str, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._redis: aioredis.Redis | None = (
            aioredis.from_url(
                keydb_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            if keydb_url
            else None
        )

    async def allow(self, chat_id: int | str) -> bool:
        if self._redis is None or self._limit <= 0:
            return True
        key = _KEY_TEMPLATE.format(chat_id=chat_id)
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, _WINDOW_SECONDS)
            return count <= self._limit
        except (RedisError, OSError):
            return True

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except (RedisError, OSError):
                pass
