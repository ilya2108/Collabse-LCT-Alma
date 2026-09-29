"""Идемпотентность POST /api/v1/send по notification_id.

Истина — KeyDB, два ключа на нотификацию:
* in-flight клейм crm:v1:notif:claim:{id} — SET NX с коротким TTL (~60с) на время
  доставки; снимается при любом исходе, а при крэше процесса истекает сам, поэтому
  ретрай relay'а доставит сообщение заново, а не потеряет его как «дубль»;
* маркер доставки crm:v1:notif:sent:{id} — SET с TTL 7 суток строго ПОСЛЕ успешной
  отправки в канал; только он давит дубли ретраев.

При недоступности KeyDB (или пустом KEYDB_URL) сервис деградирует в память процесса —
дубли внутри жизни пода всё равно давятся, а система в целом защищена уникальностью
notification.dedup_key в PG на стороне backend (data-model.md §9.4).
"""
from __future__ import annotations

import enum
import logging
import time
from collections import OrderedDict

import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_SENT_KEY_TEMPLATE = "crm:v1:notif:sent:{id}"
_CLAIM_KEY_TEMPLATE = "crm:v1:notif:claim:{id}"
_MEMORY_LIMIT = 10_000


class ClaimResult(enum.Enum):
    NEW = "new"  # первая доставка — можно отправлять
    DUPLICATE = "duplicate"  # уже доставлено — подтвердить 202 и не слать повторно
    IN_FLIGHT = "in_flight"  # доставка уже идёт (или клейм упавшей попытки ещё не истёк)


class IdempotencyStore:
    def __init__(self, keydb_url: str, ttl_seconds: int, claim_ttl_seconds: int = 60) -> None:
        self._ttl = ttl_seconds
        self._claim_ttl = claim_ttl_seconds
        self._memory: OrderedDict[str, float] = OrderedDict()
        self._inflight: dict[str, float] = {}
        self.enabled = bool(keydb_url)
        self.degraded = False
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

    def _purge_memory(self) -> None:
        now = time.monotonic()
        while self._memory:
            key, expires_at = next(iter(self._memory.items()))
            if expires_at > now and len(self._memory) <= _MEMORY_LIMIT:
                break
            self._memory.pop(key, None)
        for key in [k for k, expires_at in self._inflight.items() if expires_at <= now]:
            self._inflight.pop(key, None)

    def _remember(self, notification_id: str) -> None:
        self._memory[notification_id] = time.monotonic() + self._ttl
        self._memory.move_to_end(notification_id)

    def _mark_degraded(self, exc: Exception) -> None:
        if not self.degraded:
            logger.warning(
                "KeyDB недоступен, идемпотентность деградирует в память процесса",
                extra={"error": str(exc)},
            )
        self.degraded = True

    async def claim(self, notification_id: str) -> ClaimResult:
        """Взять in-flight клейм на доставку (короткий TTL, снимается release'ом)."""
        self._purge_memory()
        if notification_id in self._memory:
            return ClaimResult.DUPLICATE
        if self._redis is not None:
            try:
                delivered = await self._redis.exists(
                    _SENT_KEY_TEMPLATE.format(id=notification_id)
                )
                if delivered:
                    self.degraded = False
                    self._remember(notification_id)
                    return ClaimResult.DUPLICATE
                stored = await self._redis.set(
                    _CLAIM_KEY_TEMPLATE.format(id=notification_id),
                    "1",
                    nx=True,
                    ex=self._claim_ttl,
                )
                self.degraded = False
                if not stored:
                    return ClaimResult.IN_FLIGHT
                self._inflight[notification_id] = time.monotonic() + self._claim_ttl
                return ClaimResult.NEW
            except (RedisError, OSError) as exc:
                self._mark_degraded(exc)
        expires_at = self._inflight.get(notification_id)
        if expires_at is not None and expires_at > time.monotonic():
            return ClaimResult.IN_FLIGHT
        self._inflight[notification_id] = time.monotonic() + self._claim_ttl
        return ClaimResult.NEW

    async def mark_delivered(self, notification_id: str) -> None:
        """Успешная отправка: маркер доставки на полный TTL, in-flight клейм снимается."""
        self._inflight.pop(notification_id, None)
        self._remember(notification_id)
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline(transaction=False)
                pipe.set(_SENT_KEY_TEMPLATE.format(id=notification_id), "1", ex=self._ttl)
                pipe.delete(_CLAIM_KEY_TEMPLATE.format(id=notification_id))
                await pipe.execute()
                self.degraded = False
            except (RedisError, OSError) as exc:
                self._mark_degraded(exc)

    async def release(self, notification_id: str) -> None:
        """Снять in-flight клейм при неудачной отправке — ретрай relay'а доставит заново."""
        self._inflight.pop(notification_id, None)
        if self._redis is not None:
            try:
                await self._redis.delete(_CLAIM_KEY_TEMPLATE.format(id=notification_id))
            except (RedisError, OSError):
                pass

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except (RedisError, OSError):
                pass
