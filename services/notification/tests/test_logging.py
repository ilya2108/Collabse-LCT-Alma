"""Гигиена логов: маскирование адресатов, вычистка секретов, отсутствие текстов в логах."""
from __future__ import annotations

import json
import logging

from app.channels.base import OutgoingMessage
from app.channels.stubs import EmailStubChannel
from app.logging_setup import JsonFormatter, mask_recipient
from app.outbox import SendJournal


def test_mask_recipient_email() -> None:
    assert mask_recipient("ivanov@corp.local") == "i***@corp.local"


def test_mask_recipient_username_and_chat_id() -> None:
    assert mask_recipient("@ivanov_p") == "@iv***"
    assert mask_recipient("199401234") == "199***"
    assert mask_recipient("") == ""


def test_json_formatter_redacts_secret_like_fields() -> None:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="запрос к Keycloak", args=(), exc_info=None,
    )
    record.access_token = "eyJhbGciOi..."
    record.client_secret = "s3cr3t"
    record.authorization = "Bearer abc"
    record.chat_id = 199401234

    payload = json.loads(JsonFormatter().format(record))
    assert payload["access_token"] == "[redacted]"
    assert payload["client_secret"] == "[redacted]"
    assert payload["authorization"] == "[redacted]"
    assert payload["chat_id"] == 199401234  # обычные поля не трогаем


async def test_stub_channel_log_has_no_text_and_masked_recipient(
    caplog,  # noqa: ANN001 — pytest fixture
) -> None:
    journal = SendJournal()
    message = OutgoingMessage(
        notification_id="ntf-log-1",
        recipient="199401234",
        text="Заявка «МФТИ» висит на этапе «Переговоры» 15 дней.",
        subject="Заявка зависла",
        event="request.stuck",
    )
    with caplog.at_level(logging.INFO, logger="app.channels.stubs"):
        await EmailStubChannel(journal).send(message)

    record = caplog.records[-1]
    assert record.recipient == "199***"
    assert not hasattr(record, "text")
    assert record.text_length == len(message.text)
    # Полный текст сохранён в журнале для /api/v1/outbox.
    assert journal.list()[0]["text"] == message.text
