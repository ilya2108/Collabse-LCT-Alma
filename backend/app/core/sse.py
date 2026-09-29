"""SSE-хаб realtime-событий UI с fan-out через KeyDB pub/sub `crm:v1:events`.

Схема (workflow-engine.md §7, api-contract.md §2.2, Р-10):
- `publish_event(topic, data)` вызывается ПОСЛЕ коммита бизнес-транзакции;
- при живом KeyDB событие уходит в канал `crm:v1:events` — его слышат все реплики
  backend'а (каждая раздаёт своим SSE-подписчикам);
- при недоступном KeyDB событие доставляется напрямую подписчикам текущего процесса
  (single-replica деградация, CRM продолжает работать без кэша);
- кадр SSE: `event: <topic>\\ndata: <json>` (сериализация — orjson).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import orjson
from redis.exceptions import RedisError

from app.core.cache import cache_key, get_cache

logger = logging.getLogger("app.sse")

EVENTS_CHANNEL_SUFFIX = "events"  # полный канал: crm:v1:events

# максимальный размер очереди одного подписчика; медленный клиент теряет старые кадры,
# а не копит бесконечную очередь в памяти
_SUBSCRIBER_QUEUE_SIZE = 256


class SseHub:
    """Подписчики текущего процесса; потокобезопасность — один event loop."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        # True, когда фоновый слушатель KeyDB активен: тогда локальная публикация
        # не нужна (кадр придёт из pub/sub, иначе был бы дубль)
        self.listener_active: bool = False

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscribers_count(self) -> int:
        return len(self._subscribers)

    def publish_local(self, topic: str, data: dict[str, Any]) -> None:
        frame = {"topic": topic, "data": data}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                # медленный клиент: выталкиваем старейший кадр, кладём новый
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(frame)


_hub: SseHub | None = None


def get_sse_hub() -> SseHub:
    global _hub
    if _hub is None:
        _hub = SseHub()
    return _hub


def reset_sse_hub() -> None:
    global _hub
    _hub = None


async def publish_event(topic: str, data: dict[str, Any]) -> None:
    """Публикация события UI (вызывать строго после коммита транзакции)."""
    hub = get_sse_hub()
    message = orjson.dumps({"topic": topic, "data": data})
    delivered_via_keydb = False
    try:
        await get_cache().client.publish(cache_key(EVENTS_CHANNEL_SUFFIX), message)
        delivered_via_keydb = True
    except (RedisError, OSError):
        logger.debug("KeyDB недоступен на PUBLISH %s — локальная доставка", topic)
    if not delivered_via_keydb or not hub.listener_active:
        hub.publish_local(topic, data)


async def sse_listener_loop() -> None:
    """Фоновая задача: слушает `crm:v1:events` и раздаёт кадры локальным подписчикам.

    Устойчива к падению KeyDB: переподключение с паузой, на время сбоя события
    доставляются локально (см. publish_event).
    """
    hub = get_sse_hub()
    channel = cache_key(EVENTS_CHANNEL_SUFFIX)
    while True:
        pubsub = None
        try:
            pubsub = get_cache().client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel)
            hub.listener_active = True
            while True:
                message = await pubsub.get_message(timeout=5.0)
                if message is None or message.get("type") != "message":
                    continue
                try:
                    frame = orjson.loads(message["data"])
                    hub.publish_local(frame["topic"], frame.get("data") or {})
                except (orjson.JSONDecodeError, KeyError, TypeError):
                    logger.warning("SSE: некорректный кадр в pub/sub, пропущен")
        except asyncio.CancelledError:
            raise
        except (RedisError, OSError):
            logger.debug("SSE: KeyDB pub/sub недоступен, переподключение через 5 с")
            await asyncio.sleep(5)
        finally:
            hub.listener_active = False
            if pubsub is not None:
                with contextlib.suppress(RedisError, OSError):
                    await pubsub.aclose()
