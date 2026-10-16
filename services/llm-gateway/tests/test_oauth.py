"""OAuth GigaChat: формат запроса, кэш токена, обновление, один вызов на параллельные запросы."""
from __future__ import annotations

import asyncio
import uuid

import httpx

from app.providers.gigachat import GigaChatProvider
from tests.conftest import (
    ACCESS_TOKEN,
    CREDENTIALS,
    FakeGigaChat,
    chat_payload,
    oauth_ok,
    status,
)


async def test_oauth_request_format(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200, response.text

    assert len(fake.oauth_requests) == 1
    oauth = fake.oauth_requests[0]
    assert oauth.method == "POST"
    assert str(oauth.url) == "https://ngw.test:9443/api/v2/oauth"
    assert oauth.headers["Authorization"] == f"Basic {CREDENTIALS}"
    assert uuid.UUID(oauth.headers["RqUID"]).version == 4
    assert oauth.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert oauth.headers["Accept"] == "application/json"
    assert oauth.content == b"scope=GIGACHAT_API_PERS"

    chat = fake.chat_requests[0]
    assert chat.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"


async def test_rquid_is_new_for_each_oauth(build, fake: FakeGigaChat) -> None:
    _, app = await build()
    provider: GigaChatProvider = app.state.provider
    await provider.ensure_token()
    await provider.ensure_token(stale=ACCESS_TOKEN)
    ids = {request.headers["RqUID"] for request in fake.oauth_requests}
    assert len(ids) == 2


async def test_token_cached_between_calls(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    for _ in range(2):
        response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
        assert response.status_code == 200
    assert len(fake.chat_requests) == 2
    assert len(fake.oauth_requests) == 1


async def test_token_refreshed_60s_before_expiry(build, fake: FakeGigaChat, auth) -> None:
    client, app = await build()
    now = [1_760_000_000.0]  # реалистичное epoch: expires_at приходит в мс
    fake.now = lambda: now[0]
    provider: GigaChatProvider = app.state.provider
    provider.clock = lambda: now[0]

    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200
    assert len(fake.oauth_requests) == 1

    now[0] += 1800 - 61  # ещё больше минуты до истечения — токен из кэша
    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200
    assert len(fake.oauth_requests) == 1

    now[0] += 2  # меньше минуты до истечения — обновляем заранее
    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200
    assert len(fake.oauth_requests) == 2


async def test_parallel_refresh_makes_single_oauth_call(build, fake: FakeGigaChat) -> None:
    _, app = await build()

    async def slow_oauth(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return oauth_ok()

    fake.oauth_queue.append(slow_oauth)
    provider: GigaChatProvider = app.state.provider
    tokens = await asyncio.gather(*(provider.ensure_token() for _ in range(5)))
    assert set(tokens) == {ACCESS_TOKEN}
    assert len(fake.oauth_requests) == 1
    assert provider.token_cached


async def test_legacy_tok_exp_response(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.oauth_queue.append(oauth_ok("legacy-token", legacy=True))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200
    assert fake.chat_requests[0].headers["Authorization"] == "Bearer legacy-token"


async def test_oauth_401_is_auth_failed_without_retries(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.oauth_queue.append(status(401, message="Unauthorized"))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "llm_auth_failed"
    assert CREDENTIALS not in response.text
    assert len(fake.oauth_requests) == 1
    assert fake.chat_requests == []


async def test_oauth_5xx_is_retried(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.oauth_queue.extend([status(503), oauth_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200, response.text
    assert len(fake.oauth_requests) == 2
    assert response.json()["attempts"] == 2
