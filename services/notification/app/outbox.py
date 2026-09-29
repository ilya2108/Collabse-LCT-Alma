"""Журнал отправок (GET /api/v1/outbox): последние N записей на канал в памяти процесса.

Сюда попадают и заглушечные (email/max/telegram без токена), и реальные Telegram-отправки —
на демо показывает «письмо ушло» без внешних систем (api-contract.md §9.4).
"""
from __future__ import annotations

import itertools
from collections import deque
from datetime import datetime, timezone
from typing import Any


class SendJournal:
    def __init__(self, per_channel: int = 100) -> None:
        self._per_channel = per_channel
        self._channels: dict[str, deque[dict[str, Any]]] = {}
        self._seq = itertools.count(1)

    def record(
        self,
        *,
        notification_id: str,
        channel: str,
        recipient: str,
        subject: str | None,
        text: str,
        meta: dict[str, Any],
        mode: str,
        status: str = "sent",
        error: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "seq": next(self._seq),
            "notification_id": notification_id,
            "channel": channel,
            "recipient": recipient,
            "subject": subject,
            "text": text,
            "meta": meta,
            "mode": mode,  # 'real' — настоящая доставка, 'stub' — заглушка
            "status": status,  # 'sent' | 'failed' | 'skipped'
            "error": error,
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        bucket = self._channels.setdefault(channel, deque(maxlen=self._per_channel))
        bucket.append(entry)
        return entry

    def list(self, channel: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if channel is not None:
            entries: list[dict[str, Any]] = list(self._channels.get(channel, ()))
        else:
            entries = [e for bucket in self._channels.values() for e in bucket]
        entries.sort(key=lambda e: e["seq"], reverse=True)
        return entries[:limit]

    def count(self, channel: str | None = None) -> int:
        if channel is not None:
            return len(self._channels.get(channel, ()))
        return sum(len(bucket) for bucket in self._channels.values())
