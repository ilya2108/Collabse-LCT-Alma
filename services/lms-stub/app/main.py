"""Точка входа lms-stub: фабрика FastAPI-приложения и его жизненный цикл."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from . import views, webhooks
from .config import Settings
from .db import Storage
from .outbox import WebhookRelay


def create_app(
    settings: Settings | None = None, http_client: httpx.AsyncClient | None = None
) -> FastAPI:
    """Фабрика приложения; в тестах подменяются настройки и HTTP-клиент relay."""
    settings = settings or Settings.from_env()
    storage = Storage(settings.db_path)
    relay = WebhookRelay(storage=storage, settings=settings, client=http_client)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        storage.ensure_ready()
        if settings.relay_auto_start:
            await relay.start()
        yield
        await relay.stop()
        storage.close()

    app = FastAPI(
        title="LMS-заглушка (lms-stub)",
        description=(
            "Витрина двусторонней интеграции CRM↔LMS: приём push из CRM "
            "(HMAC + идемпотентность по event_id) и исходящие вебхуки lms.learning.*."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.relay = relay
    app.include_router(webhooks.router)
    app.include_router(views.router)
    return app


app = create_app()
