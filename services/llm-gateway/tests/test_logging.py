"""Гигиена логов: ни текста сообщений, ни ответа модели, ни токена, ни ключа."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.logging_setup import JsonFormatter
from tests.conftest import (
    ACCESS_TOKEN,
    CREDENTIALS,
    INTERNAL_TOKEN,
    FakeGigaChat,
    chat_ok,
    chat_payload,
    status,
)

SECRET_INPUT = "ТЕКСТ-СООБЩЕНИЯ-КОТОРЫЙ-НЕЛЬЗЯ-ЛОГИРОВАТЬ"
SECRET_OUTPUT = "ТЕКСТ-ОТВЕТА-МОДЕЛИ-КОТОРЫЙ-НЕЛЬЗЯ-ЛОГИРОВАТЬ"
FORBIDDEN = (SECRET_INPUT, SECRET_OUTPUT, ACCESS_TOKEN, CREDENTIALS, INTERNAL_TOKEN)


def _formatted(caplog: pytest.LogCaptureFixture) -> list[dict]:
    formatter = JsonFormatter()
    return [json.loads(formatter.format(record)) for record in caplog.records]


def _payload() -> dict:
    return chat_payload(
        messages=[
            {"role": "system", "content": "Только факты."},
            {"role": "user", "content": SECRET_INPUT},
        ],
        purpose="kam_summary_team",
    )


async def test_success_log_line_has_metrics_only(
    client: httpx.AsyncClient,
    fake: FakeGigaChat,
    auth: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake.chat_queue.append(chat_ok(SECRET_OUTPUT, usage=(321, 45, 366)))
    with caplog.at_level(logging.DEBUG):
        response = await client.post(
            "/v1/chat", json=_payload(), headers={**auth, "X-Request-Id": "req-log-1"}
        )
    assert response.status_code == 200

    lines = _formatted(caplog)
    dump = json.dumps(lines, ensure_ascii=False)
    for forbidden in FORBIDDEN:
        assert forbidden not in dump

    events = [line for line in lines if line.get("event") == "llm.chat"]
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == "req-log-1"
    assert event["purpose"] == "kam_summary_team"
    assert event["tier"] == "strong"
    assert event["status"] == "ok"
    assert event["attempts"] == 1
    assert event["prompt_tokens"] == 321  # счётчики токенов — не секрет
    assert event["completion_tokens"] == 45
    assert event["input_chars"] == len("Только факты.") + len(SECRET_INPUT)
    assert len(event["input_sha256"]) == 64
    assert event["structured"] is False
    assert {"queue_ms", "latency_ms", "model"} <= set(event)


async def test_error_paths_do_not_leak(
    client: httpx.AsyncClient,
    fake: FakeGigaChat,
    auth: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake.chat_queue.extend([status(400, message="bad"), status(503), status(503), status(503)])
    with caplog.at_level(logging.DEBUG):
        first = await client.post("/v1/chat", json=_payload(), headers=auth)
        second = await client.post(
            "/v1/chat", json=_payload() | {"response_schema": {"type": "object"}}, headers=auth
        )
    assert first.status_code == 502 and second.status_code == 502

    lines = _formatted(caplog)
    dump = json.dumps(lines, ensure_ascii=False)
    for forbidden in FORBIDDEN:
        assert forbidden not in dump
    statuses = [line["status"] for line in lines if line.get("event") == "llm.chat"]
    assert statuses == ["llm_upstream_error", "llm_upstream_error"]


def test_formatter_redacts_secret_like_fields_but_keeps_token_counters() -> None:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="запрос к GigaChat", args=(), exc_info=None,
    )
    record.access_token = ACCESS_TOKEN
    record.gigachat_credentials = CREDENTIALS
    record.authorization = f"Bearer {ACCESS_TOKEN}"
    record.prompt_tokens = 10
    record.completion_tokens = 5
    record.token_cached = True

    payload = json.loads(JsonFormatter().format(record))
    assert payload["access_token"] == "[redacted]"
    assert payload["gigachat_credentials"] == "[redacted]"
    assert payload["authorization"] == "[redacted]"
    assert payload["prompt_tokens"] == 10
    assert payload["completion_tokens"] == 5
    assert payload["token_cached"] is True
