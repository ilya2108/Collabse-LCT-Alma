"""Семафор на N одновременных вызовов GigaChat с ограниченной очередью (по образцу «Орбиты»).

Бесплатный тариф GIGACHAT_API_PERS держит ровно один поток: второй параллельный запрос
получает 429 от Сбера. Поэтому шлюз сам выстраивает очередь: ждущих не больше
`queue_max` (иначе 429 queue_full), ждать слот не дольше `queue_timeout` (иначе 503
queue_timeout). Счётчики видны в GET /v1/usage.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class QueueFull(Exception):
    """Ждущих уже `queue_max` — новый запрос не ставим в очередь."""


class QueueTimeout(Exception):
    """Слот не освободился за `queue_timeout` секунд."""


class ConcurrencyGate:
    """`asyncio.Semaphore` привязывается к циклу событий при первом ожидании; тесты создают
    новый цикл на каждый тест, поэтому семафор пересоздаётся при смене цикла."""

    def __init__(self, limit: int, queue_max: int, queue_timeout: float) -> None:
        self.limit = max(1, int(limit))
        self.queue_max = max(0, int(queue_max))
        self.queue_timeout = float(queue_timeout)
        self.waiting = 0
        self.in_flight = 0
        self.total = 0
        self.max_in_flight = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self.limit)
        return self._semaphore

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[int]:
        """Занять слот; отдаёт время ожидания в очереди, мс."""
        semaphore = self._get_semaphore()
        busy = semaphore.locked() or self.waiting > 0
        if busy and self.waiting >= self.queue_max:
            raise QueueFull
        started = time.monotonic()
        self.waiting += 1
        acquired = False
        try:
            try:
                async with asyncio.timeout(self.queue_timeout):
                    await semaphore.acquire()
            except TimeoutError:
                raise QueueTimeout from None
            acquired = True
            self.waiting -= 1
            self.in_flight += 1
            self.total += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            yield int((time.monotonic() - started) * 1000)
        finally:
            if acquired:
                self.in_flight -= 1
                semaphore.release()
            else:
                self.waiting -= 1

    def snapshot(self) -> dict[str, int]:
        return {
            "limit": self.limit,
            "in_flight": self.in_flight,
            "waiting": self.waiting,
            "total": self.total,
            "max_in_flight": self.max_in_flight,
        }
