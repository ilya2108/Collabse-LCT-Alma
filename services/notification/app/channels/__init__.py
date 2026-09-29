"""Адаптеры каналов доставки: telegram (реальный), email и max (заглушки)."""
from __future__ import annotations

from aiogram import Bot

from app.channels.base import ChannelAdapter, ChannelSendError, OutgoingMessage
from app.channels.stubs import EmailStubChannel, MaxStubChannel
from app.channels.telegram import TelegramChannel
from app.config import Settings
from app.metrics import Metrics
from app.outbox import SendJournal
from app.throttle import TelegramSendThrottle

__all__ = [
    "ChannelAdapter",
    "ChannelSendError",
    "OutgoingMessage",
    "build_channels",
]


def build_channels(
    settings: Settings,
    journal: SendJournal,
    bot: Bot | None,
    metrics: Metrics | None = None,
) -> dict[str, ChannelAdapter]:
    throttle = TelegramSendThrottle(
        rate_per_second=settings.telegram_send_rate_per_second,
        per_chat_interval=settings.telegram_per_chat_interval_seconds,
    )
    return {
        "telegram": TelegramChannel(
            bot=bot,
            journal=journal,
            public_url=settings.crm_public_url,
            throttle=throttle,
            metrics=metrics,
            attempts=settings.telegram_send_attempts,
            retry_base_delay=settings.telegram_retry_base_delay_seconds,
        ),
        "email": EmailStubChannel(journal=journal),
        "max": MaxStubChannel(journal=journal),
    }
