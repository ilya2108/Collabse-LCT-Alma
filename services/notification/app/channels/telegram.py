"""Канал Telegram: реальная отправка через Bot API (HTML + кнопка-ссылка в CRM).

Устойчивость: клиентский flood-control (`TelegramSendThrottle`) + ретраи на
429/сетевых/5xx ошибках Bot API с backoff; при исчерпании попыток поднимается
ChannelSendError → relay получает 502 и backend-outbox перепланирует доставку.
Без TELEGRAM_BOT_TOKEN канал работает как console-заглушка: пишет JSON-лог и запись
в журнал отправок — сценарий CI/демо без интернета (OVERVIEW.md §10 п.2).
"""
from __future__ import annotations

import asyncio
import html
import logging
from urllib.parse import urljoin

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from app.channels.base import ChannelAdapter, ChannelSendError, OutgoingMessage
from app.logging_setup import mask_recipient
from app.metrics import Metrics
from app.outbox import SendJournal
from app.throttle import TelegramSendThrottle

logger = logging.getLogger(__name__)

OPEN_IN_CRM_LABEL = "Открыть в «Альме»"

# Потолок ожидания по retry_after из 429: дольше ждать бессмысленно —
# отдаём 502, relay backend'а вернётся со своим расписанием ретраев.
_MAX_RETRY_AFTER_SECONDS = 15.0


def build_crm_url(public_url: str, url: str) -> str:
    """Абсолютная ссылка для кнопки: payload.url приходит относительным ('/requests/{id}')."""
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(public_url.rstrip("/") + "/", url.lstrip("/"))


def build_html_text(subject: str | None, text: str) -> str:
    """HTML-разметка Telegram: жирная тема + экранированный текст (защита от инъекций)."""
    body = html.escape(text)
    if subject:
        return f"<b>{html.escape(subject)}</b>\n\n{body}"
    return body


def build_reply_markup(public_url: str, meta: dict) -> InlineKeyboardMarkup | None:
    url = meta.get("url")
    if not url or not isinstance(url, str):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=OPEN_IN_CRM_LABEL, url=build_crm_url(public_url, url))]
        ]
    )


def _retry_delay(exc: TelegramAPIError, attempt: int, base_delay: float) -> float | None:
    """Пауза перед следующей попыткой; None — ошибка не ретраится (4xx кроме 429)."""
    if isinstance(exc, TelegramRetryAfter):
        return min(float(exc.retry_after), _MAX_RETRY_AFTER_SECONDS)
    if isinstance(exc, (TelegramNetworkError, TelegramServerError)):
        return base_delay * (2**attempt)  # 0.5с, 1с, 2с…
    return None


class TelegramChannel(ChannelAdapter):
    code = "telegram"

    def __init__(
        self,
        bot: Bot | None,
        journal: SendJournal,
        public_url: str,
        *,
        throttle: TelegramSendThrottle | None = None,
        metrics: Metrics | None = None,
        attempts: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self._bot = bot
        self._journal = journal
        self._public_url = public_url
        self._throttle = throttle
        self._metrics = metrics
        self._attempts = max(attempts, 1)
        self._retry_base_delay = retry_base_delay
        self.mode = "active" if bot is not None else "stub"

    async def _send_with_retries(self, chat_id: int | str, message: OutgoingMessage) -> None:
        assert self._bot is not None
        last_error: TelegramAPIError | None = None
        for attempt in range(self._attempts):
            if self._throttle is not None:
                await self._throttle.acquire(chat_id)
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=build_html_text(message.subject, message.text),
                    reply_markup=build_reply_markup(self._public_url, message.meta),
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            except TelegramAPIError as exc:
                last_error = exc
                delay = _retry_delay(exc, attempt, self._retry_base_delay)
                if delay is None or attempt == self._attempts - 1:
                    raise
                if self._metrics is not None:
                    self._metrics.mark_telegram_retry()
                logger.warning(
                    "Отправка в Telegram не удалась, повтор с паузой",
                    extra={
                        "notification_id": message.notification_id,
                        "attempt": attempt + 1,
                        "attempts_max": self._attempts,
                        "delay_seconds": round(delay, 2),
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(delay)
        if last_error is not None:  # недостижимо при attempts >= 1, защита логики
            raise last_error

    async def send(self, message: OutgoingMessage) -> None:
        if self._bot is None:
            logger.info(
                "Telegram-заглушка: токен бота не задан, сообщение записано в журнал",
                extra={
                    "notification_id": message.notification_id,
                    "channel": self.code,
                    "recipient": mask_recipient(message.recipient),
                    "text_length": len(message.text),
                    "event": message.event,
                },
            )
            self._journal.record(
                notification_id=message.notification_id,
                channel=self.code,
                recipient=message.recipient,
                subject=message.subject,
                text=message.text,
                meta=message.meta,
                mode="stub",
            )
            return

        if not message.recipient.lstrip("-").isdigit():
            # Правило tg_username даёт адрес '@ник' без chat_id: Bot API не умеет
            # писать пользователю по нику — фиксируем skipped, без ретраев relay.
            logger.info(
                "Telegram: адресат без chat_id (@ник) — доставка пропущена, "
                "нужна привязка бота через deep-link",
                extra={
                    "notification_id": message.notification_id,
                    "channel": self.code,
                    "recipient": mask_recipient(message.recipient),
                    "event": message.event,
                },
            )
            self._journal.record(
                notification_id=message.notification_id,
                channel=self.code,
                recipient=message.recipient,
                subject=message.subject,
                text=message.text,
                meta=message.meta,
                mode="real",
                status="skipped",
                error="Адрес '@ник' без chat_id: доставка возможна только после привязки бота",
            )
            return

        chat_id: int | str = int(message.recipient)
        try:
            await self._send_with_retries(chat_id, message)
        except (TelegramAPIError, ValueError) as exc:
            self._journal.record(
                notification_id=message.notification_id,
                channel=self.code,
                recipient=message.recipient,
                subject=message.subject,
                text=message.text,
                meta=message.meta,
                mode="real",
                status="failed",
                error=str(exc),
            )
            raise ChannelSendError(
                f"Не удалось доставить сообщение в Telegram: {exc}"
            ) from exc

        logger.info(
            "Сообщение доставлено в Telegram",
            extra={
                "notification_id": message.notification_id,
                "channel": self.code,
                "recipient": mask_recipient(message.recipient),
                "event": message.event,
            },
        )
        self._journal.record(
            notification_id=message.notification_id,
            channel=self.code,
            recipient=message.recipient,
            subject=message.subject,
            text=message.text,
            meta=message.meta,
            mode="real",
        )
