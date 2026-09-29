"""Каналы-заглушки Email (Exchange) и Max — осознанные stub-адаптеры по дизайну (Р-15).

Показывают подключаемость каналов: отправка структурированно логируется в JSON
и попадает в журнал GET /api/v1/outbox — «письмо ушло» видно на демо без внешних систем.
Замена на реальную интеграцию — реализация метода send без изменения контракта.
"""
from __future__ import annotations

import logging

from app.channels.base import ChannelAdapter, OutgoingMessage
from app.logging_setup import mask_recipient
from app.outbox import SendJournal

logger = logging.getLogger(__name__)


class _LoggingStubChannel(ChannelAdapter):
    mode = "stub"
    log_message: str

    def __init__(self, journal: SendJournal) -> None:
        self._journal = journal

    async def send(self, message: OutgoingMessage) -> None:
        # Полный текст — только в журнале (/api/v1/outbox под авторизацией);
        # в логах адрес маскируется, текст не пишется (гигиена ПДн).
        logger.info(
            self.log_message,
            extra={
                "notification_id": message.notification_id,
                "channel": self.code,
                "recipient": mask_recipient(message.recipient),
                "text_length": len(message.text),
                "event": message.event,
                "mode": "stub",
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


class EmailStubChannel(_LoggingStubChannel):
    code = "email"
    log_message = "Email-заглушка: письмо записано в журнал отправок"


class MaxStubChannel(_LoggingStubChannel):
    code = "max"
    log_message = "Max-заглушка: сообщение записано в журнал отправок"
