"""Общий бюджет LLM_TIMEOUT_SECONDS на все попытки, включая OAuth → 504 llm_timeout."""
from __future__ import annotations

import asyncio
import time

import httpx

from tests.conftest import FakeGigaChat, chat_ok, chat_payload, status


async def _slow_chat(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(5)
    return chat_ok()


async def test_budget_exhausted_by_slow_model(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, _ = await build(llm_timeout_seconds=0.2)
    fake.chat_queue.append(_slow_chat)
    started = time.monotonic()
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "llm_timeout"
    assert time.monotonic() - started < 2


async def test_budget_covers_oauth_and_retries(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, _ = await build(llm_timeout_seconds=0.3, llm_retry_base_delay_seconds=0.1)

    async def slow_oauth(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(503, json={"message": "busy"})

    fake.oauth_queue.extend([slow_oauth, slow_oauth, slow_oauth])
    started = time.monotonic()
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code in (502, 504)
    # бюджет кончился раньше, чем три медленные попытки OAuth
    assert time.monotonic() - started < 0.55
    assert len(fake.chat_requests) == 0


async def test_timeout_counts_in_usage(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_timeout_seconds=0.1)
    fake.chat_queue.append(_slow_chat)
    await client.post("/v1/chat", json=chat_payload(), headers=auth)
    fake.chat_queue.append(status(404))
    await client.post("/v1/chat", json=chat_payload(), headers=auth)
    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["requests"]["error"]["llm_timeout"] == 1
    assert usage["requests"]["error"]["llm_upstream_error"] == 1
    assert usage["requests"]["ok"] == 0
