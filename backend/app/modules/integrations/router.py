"""Двусторонние интеграции LMS/CMS: вебхуки с HMAC и админ-журнал с DLQ.

Контракт — api-contract.md §13: единый конверт IntegrationEvent, подпись
`X-Webhook-Signature` (или Bearer svc_integration), идемпотентность по event_id,
replay-защита 10 минут; журнал/ручной retry — §10.4.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import conflict, not_found, validation_error
from app.core.openapi import schema_ref
from app.core.pagination import ListParams, list_envelope, list_params
from app.core.security import require_permission
from app.models.service import IntegrationEvent
from app.models.user import AppUser
from app.modules.integrations import service
from app.modules.integrations.schemas import CmsWebhookAck, InboundEnvelope, LmsWebhookAck
from app.modules.integrations.security import authenticate_webhook, occurred_at_fresh

router = APIRouter(tags=["integrations"])


async def _parse_envelope(request: Request, secret: str) -> InboundEnvelope:
    raw_body = await request.body()
    await authenticate_webhook(request, raw_body, secret)
    try:
        envelope = InboundEnvelope.model_validate_json(raw_body)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise validation_error(
            "Некорректный конверт IntegrationEvent",
            details=[
                {
                    "field": ".".join(str(p) for p in first.get("loc", [])),
                    "code": first.get("type", "invalid"),
                    "message": first.get("msg", ""),
                }
            ],
        ) from exc
    return envelope


async def _check_replay(session: AsyncSession, envelope: InboundEnvelope) -> None:
    """Свежесть occurred_at ЛИБО уже виденный event_id (корректный duplicate) — §13.6."""
    if occurred_at_fresh(envelope.occurred_at):
        return
    if await service.find_seen_event(session, envelope.event_id) is not None:
        return
    raise conflict("Событие устарело (occurred_at старше 10 минут) и не является повтором")


# --- Swagger вебхуков: тело читается сырым (HMAC по байтам тела), поэтому конверт и
# заголовок подписи описаны через openapi_extra, а не параметрами обработчика

_SIGNATURE_PARAMETER: dict[str, Any] = {
    "name": "X-Webhook-Signature",
    "in": "header",
    "required": False,
    "schema": {"type": "string", "pattern": "^sha256=[0-9a-fA-F]{64}$"},
    "description": (
        "`sha256=<hex HMAC-SHA256(секрет системы, сырое тело)>`, секреты "
        "`LMS_WEBHOOK_SECRET` / `CMS_WEBHOOK_SECRET` (api-contract.md §13.6). Вместо "
        "подписи — `Authorization: Bearer` сервисного клиента с ролью `svc_integration`."
    ),
}

_EXAMPLE_IDS = {
    "event_id": "b2c3d4e5-6f70-4a1b-8c2d-3e4f5a6b7c8d",
    "request": "0b7d8a4e-1c2f-4d3e-9a5b-6c7d8e9f0a1b",
    "program": "4f3e2d1c-0b9a-4876-9543-210fedcba987",
    "product": "9a8b7c6d-5e4f-4321-8765-43210fedcba9",
}

_LMS_EXAMPLE: dict[str, Any] = {
    "event_id": _EXAMPLE_IDS["event_id"],
    "event_type": "lms.learning.completed",
    "occurred_at": "2026-11-20T09:00:00Z",
    "source": "lms",
    "correlation": {
        "crm_request_id": _EXAMPLE_IDS["request"],
        "lms_enrollment_id": "lms-enr-7781",
        "cms_lead_id": None,
    },
    "payload": {
        "status": {"code": "completed", "name": "Обучение завершено"},
        "responsible": None,
        "university": None,
        "program": {"crm_program_id": _EXAMPLE_IDS["program"], "lms_course_id": "lms-244"},
        "product": None,
        "links": {"db_keys": {"request_id": _EXAMPLE_IDS["request"]}, "s3_keys": []},
    },
}

_CMS_EXAMPLE: dict[str, Any] = {
    "event_id": _EXAMPLE_IDS["event_id"],
    "event_type": "cms.lead.created",
    "occurred_at": "2026-09-17T15:41:00Z",
    "source": "cms",
    "correlation": {"crm_request_id": None, "lms_enrollment_id": None, "cms_lead_id": "lead-5031"},
    "payload": {
        "status": {"code": "new", "name": "Новая заявка с сайта"},
        "responsible": None,
        "university": None,
        "program": {"crm_program_id": _EXAMPLE_IDS["program"], "cms_external_id": "cms-item-17"},
        "product": {"crm_product_id": _EXAMPLE_IDS["product"]},
        "links": {"db_keys": {"program_id": _EXAMPLE_IDS["program"]}, "s3_keys": []},
        "lead": {
            "client_kind": "person",
            "full_name": "Иванова Анна Сергеевна",
            "email": "anna@example.com",
            "phone": "+7 900 000-00-00",
            "company_name": None,
            "inn": None,
            "comment": "Интересует программа весной",
        },
    },
}


def _webhook_openapi(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": [_SIGNATURE_PARAMETER],
        "requestBody": {
            "required": True,
            "description": "Конверт IntegrationEvent (api-contract.md §13.1)",
            "content": {
                "application/json": {
                    "schema": schema_ref(InboundEnvelope),
                    "example": example,
                }
            },
        },
    }


_WEBHOOK_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Тело не разобрано как конверт IntegrationEvent — `validation_error`"},
    401: {
        "description": "Нет подписи, неверная подпись или Bearer без роли svc_integration — "
        "`unauthorized`"
    },
    409: {
        "description": "Событие старше 10 минут и не повтор уже принятого event_id — "
        "`conflict` (replay-защита §13.6)"
    },
}


@router.post(
    "/integrations/lms/webhook",
    summary="Вебхук LMS: lms.learning.* (HMAC X-Webhook-Signature)",
    description=(
        "Событие обучения → запись в историю заявки (`correlation.crm_request_id`) и "
        "уведомление ответственному; этап не меняется — переход делает человек. Повтор "
        "`event_id` → тот же ответ с `duplicate: true` без эффектов (§13.3, §13.5)."
    ),
    response_model=None,
    responses={
        200: {"model": LmsWebhookAck, "description": "Событие принято (или повтор)"},
        **_WEBHOOK_ERRORS,
        404: {"description": "Заявка `crm_request_id` не найдена — `not_found`"},
        422: {
            "description": "Неизвестный `event_type` или нет `correlation.crm_request_id` — "
            "`unprocessable`"
        },
    },
    openapi_extra=_webhook_openapi(_LMS_EXAMPLE),
)
async def lms_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    envelope = await _parse_envelope(request, get_settings().lms_webhook_secret)
    await _check_replay(session, envelope)
    return await service.handle_lms_event(session, envelope)


@router.post(
    "/integrations/cms/webhook",
    summary="Вебхук CMS: cms.lead.created → B2C-заявка",
    description=(
        "Лид с сайта → B2C-заявка в начальном этапе (`source: cms`), уведомление "
        "руководителю КАМов для распределения. ПДн блока `lead` шифруются при записи. "
        "Повтор `event_id` → 200 с `duplicate: true` и той же заявкой (§13.4, §13.5)."
    ),
    response_model=None,
    responses={
        201: {"model": CmsWebhookAck, "description": "Лид принят, создана B2C-заявка"},
        200: {"model": CmsWebhookAck, "description": "Повтор event_id: заявка уже создана"},
        **_WEBHOOK_ERRORS,
        422: {
            "description": "Неизвестный `event_type`, нет блока `lead` или его полей, не "
            "настроена воронка B2C — `unprocessable`"
        },
    },
    openapi_extra=_webhook_openapi(_CMS_EXAMPLE),
)
async def cms_webhook(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    envelope = await _parse_envelope(request, get_settings().cms_webhook_secret)
    await _check_replay(session, envelope)
    body, status_code = await service.handle_cms_lead(session, envelope)
    response.status_code = status_code
    return body


# --- админ-журнал и DLQ (api-contract.md §10.4) -------------------------------------------


def _serialize_event(event: IntegrationEvent) -> dict[str, Any]:
    # страховка для строк, записанных до маскирования при приёме: блок lead
    # не выдаётся с открытыми ПДн даже из старых записей (api-contract.md §12.2)
    payload = event.payload
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        payload = {**payload, "payload": service.mask_lead_payload(payload["payload"])}
    return {
        "id": str(event.id),
        "event_id": str(event.event_id),
        "direction": event.direction,
        "system": event.system,
        "event_type": event.event_type,
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id) if event.entity_id else None,
        "status": event.status,
        "attempts": event.attempts,
        "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None,
        "last_error": event.last_error,
        "payload": payload,
        "created_at": event.created_at.isoformat(),
        "processed_at": event.processed_at.isoformat() if event.processed_at else None,
    }


@router.get(
    "/admin/integration/events",
    summary="Журнал интеграционных событий",
    dependencies=[Depends(require_permission("admin:audit"))],
)
async def integration_events(
    session: Annotated[AsyncSession, Depends(get_session)],
    params: Annotated[ListParams, Depends(list_params)],
    direction: Annotated[Literal["outbound", "inbound"] | None, Query()] = None,
    system: Annotated[Literal["lms", "cms"] | None, Query()] = None,
    status: Annotated[
        Literal["pending", "retrying", "delivered", "processed", "dead", "skipped"] | None,
        Query(),
    ] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(IntegrationEvent)
    if direction:
        stmt = stmt.where(IntegrationEvent.direction == direction)
    if system:
        stmt = stmt.where(IntegrationEvent.system == system)
    if status:
        stmt = stmt.where(IntegrationEvent.status == status)
    if from_:
        stmt = stmt.where(IntegrationEvent.created_at >= from_)
    if to:
        stmt = stmt.where(IntegrationEvent.created_at < to + timedelta(days=1))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(IntegrationEvent.created_at.desc())
                .limit(params.limit)
                .offset(params.offset)
            )
        )
        .scalars()
        .all()
    )
    return list_envelope([_serialize_event(e) for e in rows], total, params)


@router.post(
    "/admin/integration/events/{event_id}/retry",
    summary="Повторить доставку события из DLQ",
)
async def retry_event(
    event_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("admin:integrations"))],
    request: Request,
) -> dict[str, Any]:
    event = await session.get(IntegrationEvent, event_id, with_for_update=True)
    if event is None:
        raise not_found("Интеграционное событие не найдено")
    if event.direction != "outbound" or event.status != "dead":
        raise conflict("Повторить можно только исходящее событие из DLQ (status=dead)")
    event.status = "retrying"
    event.attempts = 0
    event.next_retry_at = datetime.now(timezone.utc)
    event.last_error = None
    await write_audit(
        session,
        actor_user_id=user.id,
        action="integration.event_retried",
        entity_type="integration_event",
        entity_id=event.id,
        after={"event_type": event.event_type, "system": event.system},
        ip=client_ip(request),
    )
    await session.commit()
    return _serialize_event(event)
