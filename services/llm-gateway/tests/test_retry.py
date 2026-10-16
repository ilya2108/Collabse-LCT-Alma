"""Ретраи: сеть, 429, 5xx до LLM_RETRY_ATTEMPTS; Retry-After; тайм-аут чтения не повторяется."""
from __future__ import annotations

import httpx
import pytest

from app.providers.gigachat import GigaChatProvider
from tests.conftest import FakeGigaChat, chat_ok, chat_payload, status


def _record_sleeps(provider: GigaChatProvider) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    provider.sleep = fake_sleep
    return delays


async def test_429_then_ok(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.extend([status(429), chat_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["attempts"] == 2


async def test_503_503_then_ok(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.extend([status(503), status(503), chat_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["attempts"] == 3


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.RemoteProtocolError])
async def test_network_error_then_ok(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str], error: type
) -> None:
    fake.chat_queue.extend([lambda request: error("сбой сети", request=request), chat_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200, response.text
    assert len(fake.chat_requests) == 2


async def test_backoff_is_exponential(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, app = await build(llm_retry_base_delay_seconds=2.0, llm_timeout_seconds=60)
    provider: GigaChatProvider = app.state.provider
    provider.jitter = lambda low, high: 0.0
    delays = _record_sleeps(provider)
    fake.chat_queue.extend([status(502), status(504), chat_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200
    assert delays == [2.0, 4.0]


async def test_retry_after_is_respected(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, app = await build(llm_timeout_seconds=30)
    delays = _record_sleeps(app.state.provider)
    fake.chat_queue.extend([status(429, headers={"Retry-After": "7"}), chat_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200
    assert delays == [7.0]


async def test_retry_after_beyond_budget_fails_fast(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, app = await build(llm_timeout_seconds=5)
    delays = _record_sleeps(app.state.provider)
    fake.chat_queue.append(status(429, headers={"Retry-After": "60"}))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_rate_limited"
    assert response.headers["Retry-After"] == "60"
    assert delays == []
    assert len(fake.chat_requests) == 1


async def test_rate_limit_exhausted(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.extend([status(429)] * 3)
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_rate_limited"
    assert response.headers["Retry-After"] == "30"
    assert len(fake.chat_requests) == 3


async def test_5xx_exhausted_is_upstream_error(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.extend([status(500)] * 3)
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_upstream_error"
    assert len(fake.chat_requests) == 3


async def test_retry_attempts_setting(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_retry_attempts=1)
    fake.chat_queue.append(status(503))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    assert len(fake.chat_requests) == 1


async def test_read_timeout_is_not_retried(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(lambda request: httpx.ReadTimeout("долго", request=request))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "llm_timeout"
    assert len(fake.chat_requests) == 1
