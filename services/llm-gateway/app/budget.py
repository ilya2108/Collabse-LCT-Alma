"""Дневной лимит токенов GigaChat: сумма `total_tokens` за сутки по Москве.

Счётчик живёт в памяти процесса: рестарт пода его ОБНУЛЯЕТ. Для одной реплики
(Deployment llm-gateway: replicas 1, Recreate) это допустимо — лимит защищает от
«убежавшего» цикла запросов, а не ведёт бухгалтерию. Настоящий учёт — в кабинете GigaChat.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def _now_msk() -> datetime:
    return datetime.now(MSK)


class BudgetExceeded(Exception):
    """Лимит на сегодня исчерпан; `retry_after` — секунд до 00:00 МСК."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"дневной лимит токенов исчерпан, сброс через {retry_after} с")
        self.retry_after = retry_after


class DailyBudget:
    def __init__(self, cap: int, clock: Callable[[], datetime] = _now_msk) -> None:
        self.cap = max(0, int(cap))
        # часы подменяются в тестах (замороженное «сейчас»)
        self.clock = clock
        self._day: date | None = None
        self._tokens = 0

    def _now(self) -> datetime:
        return self.clock().astimezone(MSK)

    def _roll(self) -> None:
        today = self._now().date()
        if today != self._day:
            self._day = today
            self._tokens = 0

    @property
    def day(self) -> date:
        self._roll()
        assert self._day is not None
        return self._day

    @property
    def tokens_today(self) -> int:
        self._roll()
        return self._tokens

    def seconds_until_reset(self) -> int:
        now = self._now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time(0), MSK)
        return max(1, math.ceil((midnight - now).total_seconds()))

    def check(self) -> None:
        """До слота и после него: при исчерпании — BudgetExceeded с Retry-After."""
        self._roll()
        if self.cap and self._tokens >= self.cap:
            raise BudgetExceeded(self.seconds_until_reset())

    def add(self, total_tokens: int) -> None:
        """Учесть потраченные токены — в том числе ответа, который потом признан ошибкой."""
        self._roll()
        self._tokens += max(0, int(total_tokens or 0))
