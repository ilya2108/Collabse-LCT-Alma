"""KeyDB (redis-протокол): cache-aside с деградацией в PostgreSQL.

Правила — docs/design/data-model.md §10:
- все ключи с префиксом `crm:v1:`;
- инвалидация `DEL` строго после коммита транзакции;
- любой сбой KeyDB не роняет запрос — обёртки молча возвращают None/False
  (короткий connect-timeout), чтение идёт из PG;
- расшифрованные ПДн в кэш не попадают никогда.
"""

from __future__ import annotations

import logging
from typing import Any

import orjson
from redis import asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger("app.cache")

KEY_PREFIX = "crm:v1:"


def cache_key(suffix: str) -> str:
    return f"{KEY_PREFIX}{suffix}"


class Cache:
    def __init__(self, url: str, connect_timeout: float) -> None:
        self._client: aioredis.Redis = aioredis.from_url(
            url,
            socket_connect_timeout=connect_timeout,
            socket_timeout=max(connect_timeout, 0.2),
            decode_responses=False,
        )

    @property
    def client(self) -> aioredis.Redis:
        """Прямой доступ (pub/sub, GETDEL, streams) для модулей, знающих, что делают."""
        return self._client

    async def get_json(self, suffix: str) -> Any | None:
        try:
            raw = await self._client.get(cache_key(suffix))
        except (RedisError, OSError):
            logger.debug("KeyDB недоступен на GET %s — деградация в PG", suffix)
            return None
        if raw is None:
            return None
        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None

    async def set_json(self, suffix: str, value: Any, ttl_seconds: int) -> bool:
        try:
            await self._client.set(cache_key(suffix), orjson.dumps(value), ex=ttl_seconds)
            return True
        except (RedisError, OSError):
            logger.debug("KeyDB недоступен на SET %s", suffix)
            return False

    async def delete(self, *suffixes: str) -> bool:
        if not suffixes:
            return True
        try:
            await self._client.delete(*(cache_key(s) for s in suffixes))
            return True
        except (RedisError, OSError):
            logger.debug("KeyDB недоступен на DEL %s", suffixes)
            return False

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except (RedisError, OSError):
            return False

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except (RedisError, OSError):
            pass


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = Cache(settings.keydb_url, settings.keydb_connect_timeout)
    return _cache


async def close_cache() -> None:
    global _cache
    if _cache is not None:
        await _cache.close()
    _cache = None
