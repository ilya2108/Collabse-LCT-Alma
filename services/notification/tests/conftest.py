from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.channels.base import ChannelAdapter, ChannelSendError, OutgoingMessage
from app.config import Settings
from app.main import create_app

INTERNAL_TOKEN = "test-internal-token"


class FakeChannel(ChannelAdapter):
    """Тестовый адаптер: копит отправленные сообщения, по флагу — падает."""

    mode = "active"

    def __init__(self, code: str, fail: bool = False) -> None:
        self.code = code
        self.fail = fail
        self.sent: list[OutgoingMessage] = []

    async def send(self, message: OutgoingMessage) -> None:
        if self.fail:
            raise ChannelSendError("Канал недоступен (тест)")
        self.sent.append(message)


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        telegram_bot_token="",  # бот выключен: чистый relay
        internal_api_token=INTERNAL_TOKEN,
        keydb_url="",  # идемпотентность в памяти процесса
        crm_public_url="http://crm.local",
    )


@pytest.fixture()
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://notification") as client:
        yield client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": INTERNAL_TOKEN}
