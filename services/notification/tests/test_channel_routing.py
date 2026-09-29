"""Маршрутизация каналов, алиасы полей data-model и заглушка Telegram без токена."""
from __future__ import annotations

import httpx
from fastapi import FastAPI

from tests.conftest import FakeChannel


async def test_routing_to_each_channel(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    email = FakeChannel("email")
    max_channel = FakeChannel("max")
    app.state.channels["email"] = email
    app.state.channels["max"] = max_channel

    for i, channel in enumerate(("email", "max")):
        response = await client.post(
            "/api/v1/send",
            json={
                "notification_id": f"ntf-route-{i}",
                "channel": channel,
                "recipient": "someone",
                "text": "Текст",
            },
            headers=auth_headers,
        )
        assert response.status_code == 202

    assert len(email.sent) == 1
    assert len(max_channel.sent) == 1


async def test_telegram_without_token_is_stub_logged(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/send",
        json={
            "notification_id": "ntf-tg-stub",
            "channel": "telegram",
            "recipient": "199401234",
            "text": "Проверка связи",
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    outbox = (
        await client.get("/api/v1/outbox?channel=telegram", headers=auth_headers)
    ).json()
    assert outbox["total"] == 1
    assert outbox["items"][0]["mode"] == "stub"
    assert outbox["items"][0]["recipient"] == "199401234"


async def test_unknown_channel_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/send",
        json={
            "notification_id": "ntf-bad",
            "channel": "sms",
            "recipient": "x",
            "text": "y",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_data_model_aliases_accepted(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Relay может слать строку outbox-таблицы notification как есть (address/body/payload)."""
    fake = FakeChannel("email")
    app.state.channels["email"] = fake
    response = await client.post(
        "/api/v1/send",
        json={
            "notification_id": "ntf-alias",
            "channel": "email",
            "address": "head@corp.local",
            "subject": "Эскалация",
            "body": "Заявка зависла у вашего сотрудника.",
            "payload": {"request_id": "rq-2"},
            "event_type": "request.stuck",
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    message = fake.sent[0]
    assert message.recipient == "head@corp.local"
    assert message.text == "Заявка зависла у вашего сотрудника."
    assert message.meta == {"request_id": "rq-2"}
    assert message.event == "request.stuck"


async def test_template_rendering_when_text_missing(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    fake = FakeChannel("email")
    app.state.channels["email"] = fake
    response = await client.post(
        "/api/v1/send",
        json={
            "notification_id": "ntf-tpl",
            "channel": "email",
            "recipient": "kam@corp.local",
            "template": "request_stuck",
            "meta": {
                "title": "МФТИ — ДПО Data Science",
                "status": "Переговоры",
                "stuck_days": 15,
                "threshold_days": 14,
            },
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    text = fake.sent[0].text
    assert "МФТИ — ДПО Data Science" in text
    assert "15 дней" in text

    no_text = await client.post(
        "/api/v1/send",
        json={"notification_id": "ntf-no-text", "channel": "email", "recipient": "a@b.ru"},
        headers=auth_headers,
    )
    assert no_text.status_code == 400

    bad_template = await client.post(
        "/api/v1/send",
        json={
            "notification_id": "ntf-bad-tpl",
            "channel": "email",
            "recipient": "a@b.ru",
            "template": "no_such_template",
        },
        headers=auth_headers,
    )
    assert bad_template.status_code == 400
    assert "Неизвестный шаблон" in bad_template.json()["error"]["message"]
