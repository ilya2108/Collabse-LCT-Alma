"""Ретраи отправки в Telegram (429/сеть/5xx), flood-control и деградация в 502."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from app.channels.base import ChannelSendError, OutgoingMessage
from app.channels.telegram import TelegramChannel
from app.metrics import Metrics
from app.outbox import SendJournal
from app.throttle import TelegramSendThrottle

_METHOD = SendMessage(chat_id=199401234, text="x")


def _message(notification_id: str = "ntf-tg-1") -> OutgoingMessage:
    return OutgoingMessage(
        notification_id=notification_id,
        recipient="199401234",
        text="Заявка «МФТИ» висит на этапе «Переговоры» 15 дней.",
        subject="Заявка зависла",
        meta={"url": "/requests/rq-1"},
        event="request.stuck",
    )


def _channel(
    bot: Any, journal: SendJournal | None = None, metrics: Metrics | None = None
) -> TelegramChannel:
    return TelegramChannel(
        bot=bot,
        journal=journal or SendJournal(),
        public_url="http://crm.local",
        metrics=metrics,
        attempts=3,
        retry_base_delay=0.0,  # мгновенные ретраи в тестах
    )


async def test_retry_after_flood_control_then_success() -> None:
    bot = AsyncMock()
    bot.send_message.side_effect = [
        TelegramRetryAfter(_METHOD, "Too Many Requests", retry_after=0),
        None,
    ]
    metrics = Metrics()
    journal = SendJournal()
    await _channel(bot, journal, metrics).send(_message())

    assert bot.send_message.await_count == 2
    assert metrics.telegram_retries == 1
    assert journal.list()[0]["status"] == "sent"


async def test_network_errors_exhaust_attempts_and_raise_502_material() -> None:
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramNetworkError(_METHOD, "connection reset")
    metrics = Metrics()
    journal = SendJournal()

    with pytest.raises(ChannelSendError):
        await _channel(bot, journal, metrics).send(_message("ntf-tg-net"))

    assert bot.send_message.await_count == 3  # все попытки исчерпаны
    assert metrics.telegram_retries == 2  # ретраев на одну меньше, чем попыток
    entry = journal.list()[0]
    assert entry["status"] == "failed"
    assert entry["mode"] == "real"


async def test_bad_request_is_not_retried() -> None:
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramBadRequest(_METHOD, "chat not found")
    metrics = Metrics()

    with pytest.raises(ChannelSendError):
        await _channel(bot, metrics=metrics).send(_message("ntf-tg-bad"))

    assert bot.send_message.await_count == 1
    assert metrics.telegram_retries == 0


async def test_retry_after_is_capped() -> None:
    """retry_after=3600 не должен вешать relay: ждём не дольше потолка."""
    from app.channels.telegram import _MAX_RETRY_AFTER_SECONDS, _retry_delay

    exc = TelegramRetryAfter(_METHOD, "Too Many Requests", retry_after=3600)
    assert _retry_delay(exc, attempt=0, base_delay=0.5) == _MAX_RETRY_AFTER_SECONDS


async def test_throttle_spaces_out_messages_to_same_chat() -> None:
    throttle = TelegramSendThrottle(rate_per_second=1000.0, per_chat_interval=0.05)
    first = await throttle.acquire(199401234)
    second = await throttle.acquire(199401234)
    other = await throttle.acquire(5551234)

    assert first == 0.0
    assert second >= 0.04  # второй в тот же чат ждёт per_chat_interval
    assert other < 0.05  # другой чат почти не ждёт (только глобальный интервал)


async def test_throttle_disabled_with_zero_limits() -> None:
    throttle = TelegramSendThrottle(rate_per_second=0.0, per_chat_interval=0.0)
    assert await throttle.acquire(1) == 0.0
    assert await throttle.acquire(1) == 0.0
