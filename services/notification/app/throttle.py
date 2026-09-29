"""Клиентский flood-control исходящих сообщений Telegram.

Лимиты Bot API: ~30 сообщений/сек суммарно и ~1 сообщение/сек в один чат.
Держимся ниже порогов заранее, чтобы не ловить 429 TelegramRetryAfter на веерных
рассылках (эскалации по зависшим заявкам уходят пачкой из CronJob backend'а).
Резервирование слота атомарно под lock'ом, само ожидание — вне его, поэтому
отправки в разные чаты не сериализуются дольше глобального интервала.
"""
from __future__ import annotations

import asyncio
import time


class TelegramSendThrottle:
    def __init__(self, rate_per_second: float = 25.0, per_chat_interval: float = 1.0) -> None:
        self._global_interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self._per_chat_interval = max(per_chat_interval, 0.0)
        self._lock = asyncio.Lock()
        self._next_global_slot = 0.0
        self._next_chat_slot: dict[str, float] = {}

    def _purge(self, now: float) -> None:
        expired = [chat for chat, slot in self._next_chat_slot.items() if slot <= now]
        for chat in expired:
            del self._next_chat_slot[chat]

    async def acquire(self, chat_id: int | str) -> float:
        """Зарезервировать слот отправки; вернуть фактическую паузу (для тестов/логов)."""
        if self._global_interval == 0.0 and self._per_chat_interval == 0.0:
            return 0.0
        chat_key = str(chat_id)
        async with self._lock:
            now = time.monotonic()
            self._purge(now)
            slot = max(now, self._next_global_slot, self._next_chat_slot.get(chat_key, 0.0))
            self._next_global_slot = slot + self._global_interval
            self._next_chat_slot[chat_key] = slot + self._per_chat_interval
            delay = slot - now
        if delay > 0:
            await asyncio.sleep(delay)
        return delay
