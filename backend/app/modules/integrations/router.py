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
from app.core.pagination import ListParams, list_envelope, list_params
from app.core.security import require_roles
from app.models.service import IntegrationEvent
from app.models.user import AppUser
from app.modules.integrations import service
from app.modules.integrations.schemas import InboundEnvelope
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


async def _check_replay(
    session: AsyncSession, envelope: InboundEnvelope
) -> None:
    """Свежесть occurred_at ЛИБО уже виденный event_id (корректный duplicate) — §13.6."""
    if occurred_at_fresh(envelope.occurred_at):
        return
    if await service.find_seen_event(session, envelope.event_id) is not None:
        return
    raise conflict("Событие устарело (occurred_at старше 10 минут) и не является повтором")


@router.post(
    "/integrations/lms/webhook",
    summary="Вебхук LMS: lms.learning.* (HMAC X-Webhook-Signature)",
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
    dependencies=[Depends(require_roles("admin", "observer"))],
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
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
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
    user: Annotated[AppUser, Depends(require_roles("admin"))],
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
