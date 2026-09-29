"""Базовые типы адаптеров каналов."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ChannelSendError(Exception):
    """Доставка в канал не удалась; сообщение — человекочитаемое, на русском."""


@dataclass(frozen=True)
class OutgoingMessage:
    """Уже смаршрутизированное сообщение: текст отрендерен, получатель вычислен backend'ом."""

    notification_id: str
    recipient: str  # chat_id / email / @ник — колонка notification.address
    text: str
    subject: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    event: str | None = None
    template: str | None = None


class ChannelAdapter(ABC):
    """Единый интерфейс канала: telegram — реальная отправка, email/max — заглушки."""

    code: str
    mode: str  # 'active' | 'stub'

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        """Доставить сообщение; при неудаче поднять ChannelSendError (relay ретраит)."""
