"""Точка входа cms-stub: фабрика FastAPI-приложения и его жизненный цикл."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

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

    def _on_delivered(row: dict[str, Any], response: dict[str, Any] | None) -> None:
        """Ответ CRM на cms.lead.created несёт id созданной B2C-заявки —
        сохраняем его в лиде: витрина «туда-обратно» для демо.
        """
        if row.get("event_type") != "cms.lead.created" or not row.get("local_ref"):
            return
        crm_request_id = (response or {}).get("crm_request_id")
        if crm_request_id:
            storage.set_lead_crm_request(row["local_ref"], str(crm_request_id))

    relay = WebhookRelay(
        storage=storage, settings=settings, client=http_client, on_delivered=_on_delivered
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        storage.ensure_ready()
        if settings.relay_auto_start:
            await relay.start()
        yield
        await relay.stop()
        storage.close()

    app = FastAPI(
        title="CMS-заглушка (cms-stub)",
        description=(
            "Витрина двусторонней интеграции CRM↔CMS: приём каталога программ из CRM "
            "(HMAC + идемпотентность по event_id) и вебхук cms.lead.created с демо-формы сайта."
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
