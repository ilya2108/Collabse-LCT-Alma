"""Общие фикстуры: поддельный GigaChat на httpx.MockTransport и клиент шлюза через ASGI."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app

INTERNAL_TOKEN = "test-llm-token"
# base64("test-client:test-secret") — не настоящий ключ
CREDENTIALS = "dGVzdC1jbGllbnQ6dGVzdC1zZWNyZXQ="
ACCESS_TOKEN = "giga-access-token-test"
AUTH_URL = "https://ngw.test:9443/api/v2/oauth"
API_URL = "https://giga.test/api/v1"

Item = httpx.Response | Exception | Callable[[httpx.Request], Any]


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "llm_gateway_token": INTERNAL_TOKEN,
        "llm_provider": "gigachat",
        "gigachat_credentials": CREDENTIALS,
        "gigachat_auth_url": AUTH_URL,
        "gigachat_api_url": API_URL,
        "gigachat_ca_bundle": "",
        "gigachat_proxy_url": "",
        "llm_retry_base_delay_seconds": 0.0,
        "llm_timeout_seconds": 5.0,
        "llm_queue_timeout_seconds": 5.0,
        "llm_daily_token_cap": 0,
    }
    values.update(overrides)
    return Settings(**values)


def oauth_ok(
    token: str = ACCESS_TOKEN, *, expires_at: float | None = None, legacy: bool = False
) -> httpx.Response:
    expires_ms = int((expires_at if expires_at is not None else time.time() + 1800) * 1000)
    body = (
        {"tok": token, "exp": expires_ms}
        if legacy
        else {"access_token": token, "expires_at": expires_ms}
    )
    return httpx.Response(200, json=body)


def chat_ok(
    text: str = "ОК",
    *,
    model: str = "GigaChat-2-Pro",
    usage: tuple[int, int, int] = (10, 5, 15),
    finish_reason: str = "stop",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "index": 0,
                    "finish_reason": finish_reason,
                }
            ],
            "created": 1760000000,
            "model": model,
            "object": "chat.completion",
            "usage": {
                "prompt_tokens": usage[0],
                "completion_tokens": usage[1],
                "total_tokens": usage[2],
            },
        },
    )


def status(code: int, *, headers: dict[str, str] | None = None, **body: Any) -> httpx.Response:
    return httpx.Response(code, json=body or {"status": code, "message": "test"}, headers=headers)


class FakeGigaChat:
    """Обработчик MockTransport: очереди ответов OAuth и chat, журнал запросов.

    Элемент очереди — готовый Response, исключение или функция(request) → одно из них
    (в том числе корутина: для задержек и событий).
    """

    def __init__(self) -> None:
        self.oauth_requests: list[httpx.Request] = []
        self.chat_requests: list[httpx.Request] = []
        self.oauth_queue: deque[Item] = deque()
        self.chat_queue: deque[Item] = deque()
        self.now: Callable[[], float] = time.time
        self.default_chat: Callable[[httpx.Request], Any] = lambda request: chat_ok()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth"):
            self.oauth_requests.append(request)
            item: Any = (
                self.oauth_queue.popleft()
                if self.oauth_queue
                else oauth_ok(expires_at=self.now() + 1800)
            )
        elif request.url.path.endswith("/chat/completions"):
            self.chat_requests.append(request)
            item = self.chat_queue.popleft() if self.chat_queue else self.default_chat
        else:
            return httpx.Response(404, json={"message": "not found"})
        if callable(item) and not isinstance(item, httpx.Response):
            item = item(request)
            if inspect.isawaitable(item):
                item = await item
        if isinstance(item, Exception):
            raise item
        return item

    def chat_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.chat_requests]


def chat_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "Ты помощник."},
            {"role": "user", "content": "Скажи ОК"},
        ],
        "tier": "strong",
        "temperature": 0.2,
        "max_tokens": 300,
        "purpose": "test_case",
    }
    payload.update(overrides)
    return payload


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось вовремя")
        await asyncio.sleep(0.005)


@pytest.fixture()
def fake() -> FakeGigaChat:
    return FakeGigaChat()


@pytest.fixture()
def auth() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN}


Builder = Callable[..., Awaitable[tuple[httpx.AsyncClient, FastAPI]]]


@pytest.fixture()
async def build(fake: FakeGigaChat) -> AsyncIterator[Builder]:
    """Фабрика: шлюз с переопределёнными настройками поверх поддельного GigaChat."""
    created: list[tuple[httpx.AsyncClient, FastAPI]] = []

    async def _build(**overrides: Any) -> tuple[httpx.AsyncClient, FastAPI]:
        app = create_app(make_settings(**overrides), transport=httpx.MockTransport(fake))
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://llm-gateway"
        )
        created.append((client, app))
        return client, app

    yield _build
    for client, app in created:
        await client.aclose()
        await app.state.http.aclose()


@pytest.fixture()
async def gateway(build: Builder) -> tuple[httpx.AsyncClient, FastAPI]:
    return await build()


@pytest.fixture()
def client(gateway: tuple[httpx.AsyncClient, FastAPI]) -> httpx.AsyncClient:
    return gateway[0]


@pytest.fixture()
def app(gateway: tuple[httpx.AsyncClient, FastAPI]) -> FastAPI:
    return gateway[1]
