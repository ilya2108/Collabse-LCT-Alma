"""Точка входа: FastAPI-приложение (модульный монолит).

Модули — OVERVIEW.md §4.1; все бизнес-роуты под префиксом /api/v1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.cache import close_cache
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import RequestIdMiddleware, register_error_handlers
from app.core.health import router as health_router
from app.core.outbox import outbox_relay_loop
from app.core.sse import sse_listener_loop
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.crm.router import router as crm_router
from app.modules.files.router import router as files_router
from app.modules.import_export.router import router as import_export_router
from app.modules.integrations.router import router as integrations_router
from app.modules.internal.router import router as internal_router
from app.modules.notifications.router import router as notifications_router
from app.modules.reporting.router import router as reporting_router
from app.modules.requests.router import router as requests_router
from app.modules.talent_pool.router import router as talent_pool_router
from app.modules.ui.router import router as ui_router
from app.modules.workflow.router import router as workflow_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    # фоновые таски: relay outbox'ов (нотификации + интеграции) и слушатель
    # KeyDB pub/sub для SSE; в тестах не стартуют (нет PG/KeyDB)
    background_tasks: list[asyncio.Task] = []
    if settings.app_env != "test":
        background_tasks = [
            asyncio.create_task(outbox_relay_loop(), name="outbox-relay"),
            asyncio.create_task(sse_listener_loop(), name="sse-listener"),
        ]
    yield
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await close_cache()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="CRM «Вузы» — API",
        version="1.0.0",
        description=(
            "CRM для работы с вузами (ЛЦТ 2026): workflow B2B/B2C, договоры, "
            "импорт/экспорт, пул талантов, интеграции LMS/CMS, нотификации."
        ),
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    app.include_router(health_router)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_router)
    api.include_router(ui_router)
    api.include_router(crm_router)
    api.include_router(requests_router)
    api.include_router(workflow_router)
    api.include_router(import_export_router)
    api.include_router(reporting_router)
    api.include_router(talent_pool_router)
    api.include_router(notifications_router)
    api.include_router(files_router)
    api.include_router(integrations_router)
    api.include_router(admin_router)
    api.include_router(internal_router)
    app.include_router(api)

    return app


app = create_app()
