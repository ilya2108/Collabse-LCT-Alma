"""Точка входа llm-gateway (порт 8040, один процесс uvicorn).

Единственный компонент, который знает ключ GigaChat и ходит к нему наружу — второй под
с egress после notification (NetworkPolicy 41-llm-gateway-dmz: только TCP 443/9443 вне
приватных сетей). Шлюз «глупый»: принимает сообщения и отдаёт текст и usage; промпт,
разбор JSON, проверка чисел и псевдонимы КАМов — в backend (модуль ai_summary).

Один процесс и одна реплика — осознанно: семафор (LLM_MAX_CONCURRENCY=1 под бесплатный
тариф) и дневной лимит токенов живут в памяти процесса.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI

from app.budget import DailyBudget
from app.config import Settings
from app.errors import register_error_handlers
from app.gate import ConcurrencyGate
from app.logging_setup import setup_logging
from app.metrics import CallStats
from app.providers import build_provider
from app.routes import router
from app.tls import build_http_client, build_tls

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        yield
    finally:
        await app.state.provider.aclose()
        await app.state.http.aclose()


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Альма — llm-gateway",
        description="Шлюз к GigaChat: ключ, очередь на один поток, дневной лимит, TLS НУЦ",
        version="1.0.0",
        lifespan=_lifespan,
    )

    tls = build_tls(settings)
    http = build_http_client(settings, tls, transport)
    provider = build_provider(settings, http)

    app.state.settings = settings
    app.state.tls = tls
    app.state.http = http
    app.state.provider = provider
    app.state.gate = ConcurrencyGate(
        settings.llm_max_concurrency, settings.llm_queue_max, settings.llm_queue_timeout_seconds
    )
    app.state.budget = DailyBudget(settings.llm_daily_token_cap)
    app.state.stats = CallStats()

    register_error_handlers(app)
    app.include_router(router)

    if settings.dev_token_with_real_key:
        logger.error(
            "llm-gateway: задан ключ GigaChat, но LLM_GATEWAY_TOKEN пуст или равен dev-дефолту — "
            "все вызовы /v1/chat отклоняются (401). Задайте случайный токен: make dev-env "
            "или make llm-secret"
        )

    logger.info(
        "llm-gateway сконфигурирован",
        extra={
            "provider": provider.name,
            "configured": provider.configured,
            "api_host": urlsplit(settings.gigachat_api_url).netloc,
            "auth_host": urlsplit(settings.gigachat_auth_url).netloc,
            "ca_bundle": tls.ca_bundle,
            "model_strong": settings.gigachat_model_strong,
            "model_fast": settings.gigachat_model_fast,
            "max_concurrency": settings.llm_max_concurrency,
            "queue_max": settings.llm_queue_max,
            "daily_token_cap": settings.llm_daily_token_cap,
            "proxy": bool(settings.gigachat_proxy_url),
        },
    )
    return app


app = create_app()
