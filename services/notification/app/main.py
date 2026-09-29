"""Точка входа notification-service (порт 8010).

Два контура в одном процессе (deployment.md §2.3):
1. HTTP API — relay нотификаций из outbox backend'а в адаптеры каналов
   (telegram — реальный, email/max — заглушки) + журнал отправок + health.
2. Telegram-бот aiogram (long-polling) — включается только при TELEGRAM_BOT_TOKEN;
   без токена сервис остаётся чистым relay (CI/демо без интернета).

Единственный egress в интернет во всём контуре — этот сервис (DMZ-стори, OVERVIEW.md §5).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.enums import ParseMode
from fastapi import FastAPI

from app.api.routes import router
from app.backend_client import BackendClient
from app.bot.runner import BotRunner, create_dispatcher
from app.channels import build_channels
from app.config import Settings
from app.errors import register_error_handlers
from app.idempotency import IdempotencyStore
from app.keycloak import KeycloakJWTVerifier, KeycloakTokenManager
from app.logging_setup import setup_logging
from app.metrics import Metrics
from app.outbox import SendJournal
from app.ratelimit import TelegramRateLimiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    runner: BotRunner | None = app.state.bot_runner
    if runner is not None:
        await runner.start()
    try:
        yield
    finally:
        if runner is not None:
            await runner.stop()
        await app.state.idempotency.close()
        await app.state.rate_limiter.close()
        await app.state.http.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Альма — Notification Service",
        description="Relay каналов нотификаций (telegram/email/max) + Telegram-бот",
        version="1.0.0",
        lifespan=_lifespan,
    )

    http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    bot: Bot | None = None
    if settings.bot_enabled:
        # Запас клиентского таймаута над серверным удержанием long-poll (25 с):
        # через CONNECT-прокси задержки складываются, и таймаут впритык
        # регулярно убивает getUpdates (наблюдалось на VPS-стенде).
        session = None
        if settings.telegram_api_base or settings.telegram_proxy_url:
            api = (
                TelegramAPIServer.from_base(settings.telegram_api_base.rstrip("/"))
                if settings.telegram_api_base
                else PRODUCTION
            )
            session = AiohttpSession(
                api=api,
                proxy=settings.telegram_proxy_url or None,
                timeout=90,
            )
        bot = Bot(
            token=settings.telegram_bot_token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    journal = SendJournal(per_channel=settings.outbox_per_channel)
    metrics = Metrics()
    tokens = KeycloakTokenManager(
        token_endpoint=settings.token_endpoint,
        client_id=settings.kc_client_id,
        client_secret=settings.kc_client_secret,
        http=http,
    )
    backend = BackendClient(base_url=settings.backend_base_url, tokens=tokens, http=http)
    rate_limiter = TelegramRateLimiter(
        settings.keydb_url, settings.bot_rate_limit_per_minute
    )

    app.state.settings = settings
    app.state.http = http
    app.state.journal = journal
    app.state.metrics = metrics
    app.state.idempotency = IdempotencyStore(
        settings.keydb_url,
        settings.idempotency_ttl_seconds,
        settings.idempotency_claim_ttl_seconds,
    )
    app.state.rate_limiter = rate_limiter
    app.state.keycloak_tokens = tokens
    app.state.jwt_verifier = KeycloakJWTVerifier(
        jwks_endpoint=settings.jwks_endpoint,
        allowed_issuers=settings.allowed_issuers,
        http=http,
    )
    app.state.channels = build_channels(settings, journal, bot, metrics)
    app.state.bot_runner = (
        BotRunner(bot, create_dispatcher(backend, rate_limiter, metrics))
        if bot is not None
        else None
    )

    register_error_handlers(app)
    app.include_router(router)

    logger.info(
        "notification-service сконфигурирован",
        extra={
            "bot_enabled": settings.bot_enabled,
            "backend": settings.backend_base_url,
            "keydb": bool(settings.keydb_url),
        },
    )
    return app


app = create_app()
