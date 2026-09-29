"""Маршруты: приём нотификаций от backend-relay, журнал отправок, health-пробы."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.bot.deeplink import build_deep_link
from app.channels.base import ChannelSendError, OutgoingMessage
from app.errors import ApiError
from app.idempotency import ClaimResult
from app.keycloak import KeycloakError
from app.schemas import (
    BotStats,
    Channel,
    HealthResponse,
    OutboxResponse,
    ReadyResponse,
    SendRequest,
    SendResponse,
    StatsResponse,
)
from app.security import require_internal_token, require_outbox_access
from app.templates import UnknownTemplateError, known_templates, render_template

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_text(payload: SendRequest) -> str:
    """Готовый text от backend приоритетен; иначе рендер шаблона §9.4 из meta."""
    if payload.text:
        return payload.text
    context = dict(payload.meta)
    context.setdefault("channel", payload.channel)
    try:
        return render_template(payload.template or "", context)
    except UnknownTemplateError:
        raise ApiError(
            400,
            "validation_error",
            f"Неизвестный шаблон «{payload.template}». "
            f"Доступные: {', '.join(known_templates())}",
        ) from None


@router.post(
    "/api/v1/send",
    status_code=202,
    response_model=SendResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Принять нотификацию из outbox backend'а и доставить в канал",
)
async def send_notification(payload: SendRequest, request: Request) -> SendResponse:
    state = request.app.state
    adapter = state.channels[payload.channel]
    text = _resolve_text(payload)

    claimed = await state.idempotency.claim(payload.notification_id)
    if claimed is ClaimResult.DUPLICATE:
        state.metrics.mark_duplicate()
        logger.info(
            "Дубль нотификации отброшен по идемпотентности",
            extra={"notification_id": payload.notification_id, "channel": payload.channel},
        )
        return SendResponse()
    if claimed is ClaimResult.IN_FLIGHT:
        # Доставка уже идёт (или клейм упавшей попытки ещё не истёк по короткому TTL).
        # Подтверждать 202 нельзя: relay пометил бы sent недоставленное сообщение.
        logger.warning(
            "Нотификация уже в доставке — relay повторит попытку позже",
            extra={"notification_id": payload.notification_id, "channel": payload.channel},
        )
        raise ApiError(
            409,
            "delivery_in_progress",
            "Доставка этой нотификации уже выполняется, повторите попытку позже",
        )

    message = OutgoingMessage(
        notification_id=payload.notification_id,
        recipient=payload.recipient,
        text=text,
        subject=payload.subject,
        meta=payload.meta,
        event=payload.event,
        template=payload.template,
    )
    delivered = False
    try:
        await adapter.send(message)
        delivered = True
    except ChannelSendError as exc:
        state.metrics.mark_failed(payload.channel)
        raise ApiError(502, "channel_send_failed", str(exc)) from exc
    finally:
        if delivered:
            # Маркер «доставлено» ставится только после успешной отправки в канал.
            await state.idempotency.mark_delivered(payload.notification_id)
        else:
            # Любое исключение (включая CancelledError при shutdown) снимает in-flight
            # клейм: ретрай relay'а доставит заново, а не отбросится как «дубль».
            await state.idempotency.release(payload.notification_id)
    state.metrics.mark_sent(payload.channel)
    return SendResponse()


@router.get(
    "/api/v1/outbox",
    response_model=OutboxResponse,
    dependencies=[Depends(require_outbox_access)],
    summary="Журнал отправок (включая заглушечные email/max) — отладка и демо",
)
@router.get(
    "/api/v1/stub-outbox",
    response_model=OutboxResponse,
    dependencies=[Depends(require_outbox_access)],
    include_in_schema=False,  # алиас канонического /api/v1/outbox
)
async def outbox(
    request: Request,
    channel: Annotated[Channel | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> OutboxResponse:
    journal = request.app.state.journal
    return OutboxResponse(
        items=journal.list(channel, limit),
        total=journal.count(channel),
        limit=limit,
    )


@router.get(
    "/api/v1/stats",
    response_model=StatsResponse,
    dependencies=[Depends(require_outbox_access)],
    summary="Операционные счётчики: отправки по каналам, ошибки, ретраи, привязки бота",
)
async def stats(request: Request) -> StatsResponse:
    """Счётчики с момента старта процесса (метрики для защиты и smoke-мониторинга)."""
    state = request.app.state
    settings = state.settings
    snapshot = state.metrics.snapshot()
    username = settings.telegram_bot_username or None
    deep_link_template: str | None = None
    if username:
        try:
            deep_link_template = build_deep_link(username, "<code>")
        except ValueError:
            deep_link_template = None
    return StatsResponse(
        started_at=snapshot["started_at"],
        uptime_seconds=snapshot["uptime_seconds"],
        channels={code: adapter.mode for code, adapter in state.channels.items()},
        sent=snapshot["sent"],
        failed=snapshot["failed"],
        duplicates_suppressed=snapshot["duplicates_suppressed"],
        telegram_retries=snapshot["telegram_retries"],
        bot=BotStats(
            enabled=settings.bot_enabled,
            username=username,
            deep_link_template=deep_link_template,
            **snapshot["bot"],
        ),
    )


# --- Health-пробы (без аутентификации, api-contract.md §1.7 + deployment.md §3.3) -----

@router.get("/healthz", response_model=HealthResponse, include_in_schema=False)
@router.get("/health/live", response_model=HealthResponse, include_in_schema=False)
@router.get("/healthz/live", response_model=HealthResponse, include_in_schema=False)
async def healthz() -> HealthResponse:
    return HealthResponse()


@router.get("/readyz", include_in_schema=False)
@router.get("/health/ready", include_in_schema=False)
@router.get("/healthz/ready", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    """Readiness: в режиме relay сервис готов всегда; с ботом — polling жив и токен KC есть."""
    state = request.app.state
    settings = state.settings
    idempotency = state.idempotency
    if not idempotency.enabled:
        keydb_status = "disabled"
    elif idempotency.degraded:
        keydb_status = "degraded"
    else:
        keydb_status = "ok"
    checks: dict[str, str] = {"keydb": keydb_status}
    ready = True

    if settings.bot_enabled:
        runner = state.bot_runner
        checks["bot"] = "running" if runner is not None and runner.running else "stopped"
        ready = ready and checks["bot"] == "running"
        try:
            await state.keycloak_tokens.get_token()
            checks["keycloak"] = "ok"
        except KeycloakError as exc:
            checks["keycloak"] = f"error: {exc}"
            ready = False
    else:
        checks["bot"] = "disabled"
        checks["keycloak"] = "not_required"

    body = ReadyResponse(status="ok" if ready else "unavailable", checks=checks)
    return JSONResponse(status_code=200 if ready else 503, content=body.model_dump())
