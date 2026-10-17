"""Семафор и очередь: один поток к GigaChat, 429 queue_full, 503 queue_timeout."""
from __future__ import annotations

import asyncio

import httpx

from app.gate import ConcurrencyGate, QueueFull
from tests.conftest import FakeGigaChat, chat_ok, chat_payload, wait_until


async def test_single_slot_serializes_calls(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return chat_ok()

    fake.default_chat = slow
    first, second = await asyncio.gather(
        client.post("/v1/chat", json=chat_payload(), headers=auth),
        client.post("/v1/chat", json=chat_payload(), headers=auth),
    )
    assert first.status_code == second.status_code == 200
    assert max(first.json()["queue_ms"], second.json()["queue_ms"]) >= 50

    gate = (await client.get("/v1/usage", headers=auth)).json()["gate"]
    assert gate["limit"] == 1
    assert gate["max_in_flight"] == 1
    assert gate["total"] == 2
    assert gate["in_flight"] == 0 and gate["waiting"] == 0


async def test_queue_full_is_429(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_queue_max=0)
    release = asyncio.Event()

    async def blocked(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return chat_ok()

    fake.chat_queue.append(blocked)
    first = asyncio.create_task(client.post("/v1/chat", json=chat_payload(), headers=auth))
    await wait_until(lambda: len(fake.chat_requests) == 1)

    second = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "queue_full"
    assert second.headers["Retry-After"] == "30"

    release.set()
    assert (await first).status_code == 200


async def test_queue_timeout_is_503(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_queue_timeout_seconds=0.1)
    release = asyncio.Event()

    async def blocked(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return chat_ok()

    fake.chat_queue.append(blocked)
    first = asyncio.create_task(client.post("/v1/chat", json=chat_payload(), headers=auth))
    await wait_until(lambda: len(fake.chat_requests) == 1)

    second = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert second.status_code == 503
    assert second.json()["error"]["code"] == "queue_timeout"
    assert second.headers["Retry-After"] == "30"

    release.set()
    assert (await first).status_code == 200
    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["gate"]["waiting"] == 0
    assert usage["requests"]["error"]["queue_timeout"] == 1


async def test_gate_counters_unit() -> None:
    gate = ConcurrencyGate(limit=1, queue_max=1, queue_timeout=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with gate.slot():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()

    async def wait_in_queue() -> int:
        async with gate.slot() as queue_ms:
            return queue_ms

    waiter = asyncio.create_task(wait_in_queue())
    await wait_until(lambda: gate.waiting == 1)
    assert gate.snapshot()["in_flight"] == 1

    try:
        async with gate.slot():
            raise AssertionError("третий запрос не должен пройти: очередь заполнена")
    except QueueFull:
        pass

    release.set()
    await holder
    assert await waiter >= 0
    assert gate.snapshot() == {
        "limit": 1, "in_flight": 0, "waiting": 0, "total": 2, "max_in_flight": 1,
    }
