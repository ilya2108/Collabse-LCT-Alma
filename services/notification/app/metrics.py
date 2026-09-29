"""Счётчики сервиса для GET /api/v1/stats — цифры для защиты и smoke-мониторинга.

Всё в памяти процесса и в одном event loop: обычные int-инкременты без блокировок.
Обнуляются при рестарте пода — это операционные счётчики, не бизнес-аналитика
(история отправок живёт в outbox-таблице `notification` в PG на стороне backend).
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self._started_monotonic = time.monotonic()
        self.started_at = datetime.now(timezone.utc)
        self.sent_by_channel: Counter[str] = Counter()
        self.failed_by_channel: Counter[str] = Counter()
        self.duplicates_suppressed = 0
        self.telegram_retries = 0
        self.bot_commands: Counter[str] = Counter()
        self.bot_rate_limited = 0
        self.bindings_created = 0
        self.bindings_removed = 0

    # --- Инкременты (вызываются из routes / каналов / бота) ---------------------------

    def mark_sent(self, channel: str) -> None:
        self.sent_by_channel[channel] += 1

    def mark_failed(self, channel: str) -> None:
        self.failed_by_channel[channel] += 1

    def mark_duplicate(self) -> None:
        self.duplicates_suppressed += 1

    def mark_telegram_retry(self) -> None:
        self.telegram_retries += 1

    def mark_bot_command(self, command: str) -> None:
        self.bot_commands[command] += 1

    def mark_bot_rate_limited(self) -> None:
        self.bot_rate_limited += 1

    def mark_binding_created(self) -> None:
        self.bindings_created += 1

    def mark_binding_removed(self) -> None:
        self.bindings_removed += 1

    # --- Снимок для GET /api/v1/stats -------------------------------------------------

    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self._started_monotonic)

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_seconds": self.uptime_seconds(),
            "sent": dict(self.sent_by_channel),
            "failed": dict(self.failed_by_channel),
            "duplicates_suppressed": self.duplicates_suppressed,
            "telegram_retries": self.telegram_retries,
            "bot": {
                "commands": dict(self.bot_commands),
                "commands_total": sum(self.bot_commands.values()),
                "rate_limited": self.bot_rate_limited,
                "bindings_created": self.bindings_created,
                "bindings_removed": self.bindings_removed,
            },
        }
