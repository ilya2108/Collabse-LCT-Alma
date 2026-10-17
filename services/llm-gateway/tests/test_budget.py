"""Дневной лимит токенов: сутки по МСК, Retry-After до полуночи, учёт неуспешных ответов."""
from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest

from app.budget import MSK, BudgetExceeded, DailyBudget
from tests.conftest import FakeGigaChat, chat_ok, chat_payload, wait_until


def _frozen(moment: list[datetime]):
    return lambda: moment[0]


async def test_cap_exceeded_returns_retry_after_until_msk_midnight(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, app = await build(llm_daily_token_cap=20)
    now = [datetime(2026, 10, 16, 23, 59, 0, tzinfo=MSK)]
    app.state.budget.clock = _frozen(now)
    fake.default_chat = lambda request: chat_ok(usage=(10, 5, 15))

    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200
    # 15 < 20 — второй вызов ещё проходит, после него 30 ≥ 20
    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200

    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_budget_exceeded"
    assert response.headers["Retry-After"] == "60"
    assert len(fake.chat_requests) == 2

    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["day"] == "2026-10-16"
    assert usage["tokens_today"] == 30
    assert usage["daily_cap"] == 20

    # новые сутки по Москве — счётчик с нуля
    now[0] = datetime(2026, 10, 17, 0, 0, 1, tzinfo=MSK)
    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200
    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["day"] == "2026-10-17"
    assert usage["tokens_today"] == 15


async def test_failed_response_with_usage_is_counted(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, _ = await build(llm_daily_token_cap=1000)
    fake.chat_queue.append(
        httpx.Response(200, json={"choices": [], "usage": {"total_tokens": 42}})
    )
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["tokens_today"] == 42


async def test_budget_rechecked_after_waiting_for_slot(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, _ = await build(llm_daily_token_cap=10)
    release = asyncio.Event()

    async def blocked(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return chat_ok(usage=(10, 5, 15))

    fake.chat_queue.append(blocked)
    first = asyncio.create_task(client.post("/v1/chat", json=chat_payload(), headers=auth))
    await wait_until(lambda: len(fake.chat_requests) == 1)
    # второй прошёл проверку лимита до слота (0 < 10) и ждёт в очереди
    second = asyncio.create_task(client.post("/v1/chat", json=chat_payload(), headers=auth))
    await asyncio.sleep(0.05)
    release.set()

    assert (await first).status_code == 200
    response = await second
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_budget_exceeded"
    assert len(fake.chat_requests) == 1


def test_daily_budget_unit() -> None:
    now = [datetime(2026, 10, 16, 12, 0, 0, tzinfo=MSK)]
    budget = DailyBudget(100, clock=_frozen(now))
    budget.add(60)
    budget.check()
    budget.add(40)
    with pytest.raises(BudgetExceeded) as caught:
        budget.check()
    assert caught.value.retry_after == 12 * 3600

    # часы в UTC: 21:30 UTC — это уже 00:30 следующих суток по Москве
    from datetime import UTC

    now[0] = datetime(2026, 10, 16, 21, 30, 0, tzinfo=UTC)
    budget.check()
    assert budget.day.isoformat() == "2026-10-17"
    assert budget.tokens_today == 0


def test_zero_cap_means_unlimited() -> None:
    budget = DailyBudget(0)
    budget.add(10**9)
    budget.check()
