"""Идемпотентность POST /api/v1/send по notification_id и аутентификация контура."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.channels.base import OutgoingMessage
from tests.conftest import FakeChannel


def _body(notification_id: str = "ntf-1", **overrides) -> dict:
    body = {
        "notification_id": notification_id,
        "channel": "email",
        "recipient": "kam@corp.local",
        "subject": "Заявка зависла",
        "text": "Заявка «МФТИ» висит на этапе «Переговоры» 15 дней.",
        "meta": {"request_id": "rq-1", "url": "/requests/rq-1"},
    }
    body.update(overrides)
    return body


async def test_duplicate_notification_delivered_once(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    fake = FakeChannel("email")
    app.state.channels["email"] = fake

    first = await client.post("/api/v1/send", json=_body(), headers=auth_headers)
    second = await client.post("/api/v1/send", json=_body(), headers=auth_headers)

    assert first.status_code == 202
    assert first.json() == {"accepted": True}
    assert second.status_code == 202
    assert second.json() == {"accepted": True}
    assert len(fake.sent) == 1  # дубль отброшен, доставка одна


async def test_distinct_ids_delivered_separately(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    fake = FakeChannel("email")
    app.state.channels["email"] = fake

    await client.post("/api/v1/send", json=_body("ntf-a"), headers=auth_headers)
    await client.post("/api/v1/send", json=_body("ntf-b"), headers=auth_headers)

    assert [m.notification_id for m in fake.sent] == ["ntf-a", "ntf-b"]


async def test_failed_send_releases_claim_for_retry(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    fake = FakeChannel("email", fail=True)
    app.state.channels["email"] = fake

    failed = await client.post("/api/v1/send", json=_body("ntf-retry"), headers=auth_headers)
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "channel_send_failed"

    fake.fail = False  # relay backend'а ретраит ту же запись
    retried = await client.post("/api/v1/send", json=_body("ntf-retry"), headers=auth_headers)
    assert retried.status_code == 202
    assert len(fake.sent) == 1


async def test_crash_mid_send_releases_claim_for_retry(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Не-ChannelSendError посреди отправки не должен «съесть» ретрай как дубль."""

    class CrashingChannel(FakeChannel):
        def __init__(self, code: str) -> None:
            super().__init__(code)
            self.crash_once = True

        async def send(self, message: OutgoingMessage) -> None:
            if self.crash_once:
                self.crash_once = False
                raise RuntimeError("процесс упал посреди отправки (тест)")
            await super().send(message)

    fake = CrashingChannel("email")
    app.state.channels["email"] = fake

    with pytest.raises(RuntimeError):
        await client.post("/api/v1/send", json=_body("ntf-crash"), headers=auth_headers)

    retried = await client.post("/api/v1/send", json=_body("ntf-crash"), headers=auth_headers)
    assert retried.status_code == 202
    assert len(fake.sent) == 1  # ретрай relay'а доставил, клейм не завис


async def test_inflight_duplicate_not_confirmed_as_sent(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Пока доставка идёт, дубль получает 409 (retry), а не 202 (relay пометил бы sent)."""
    started = asyncio.Event()
    unblock = asyncio.Event()

    class SlowChannel(FakeChannel):
        async def send(self, message: OutgoingMessage) -> None:
            started.set()
            await unblock.wait()
            await super().send(message)

    fake = SlowChannel("email")
    app.state.channels["email"] = fake

    first = asyncio.create_task(
        client.post("/api/v1/send", json=_body("ntf-slow"), headers=auth_headers)
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    conflict = await client.post("/api/v1/send", json=_body("ntf-slow"), headers=auth_headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "delivery_in_progress"

    unblock.set()
    assert (await first).status_code == 202

    duplicate = await client.post("/api/v1/send", json=_body("ntf-slow"), headers=auth_headers)
    assert duplicate.status_code == 202
    assert len(fake.sent) == 1  # доставка ровно одна


async def test_send_requires_internal_token(client: httpx.AsyncClient) -> None:
    missing = await client.post("/api/v1/send", json=_body())
    wrong = await client.post(
        "/api/v1/send", json=_body(), headers={"X-Internal-Token": "wrong"}
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"
    assert wrong.status_code == 401


async def test_outbox_alias_and_auth(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post("/api/v1/send", json=_body("ntf-outbox"), headers=auth_headers)

    unauthorized = await client.get("/api/v1/outbox")
    assert unauthorized.status_code == 401

    for path in ("/api/v1/outbox", "/api/v1/stub-outbox"):
        response = await client.get(path, headers=auth_headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["notification_id"] == "ntf-outbox"
        assert payload["items"][0]["channel"] == "email"
        assert payload["items"][0]["mode"] == "stub"
