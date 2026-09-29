"""Обработчики входящих интеграционных событий LMS/CMS (api-contract.md §13.3–13.5).

Идемпотентность приёма: быстрый guard KeyDB `crm:v1:intg:seen:{event_id}` (SET NX,
7 суток) + истина в PG (`integration_event.event_id` UNIQUE). Повтор `event_id` —
тот же ответ с `duplicate: true`, побочные эффекты не выполняются.

Резолюция (data-model > api-contract): `deal.responsible_user_id NOT NULL`, поэтому
лид CMS «без ответственного» назначается первому активному head_kam (распределение —
его задача, уведомление `integration.lead_received` уходит роли head_kam), фолбэк —
admin.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_key, get_cache
from app.core.errors import not_found, unprocessable
from app.core.events import (
    EVENT_INTEGRATION_LEAD_RECEIVED,
    INTG_CMS_LEAD_CREATED,
    LMS_INBOUND_EVENTS,
)
from app.core.outbox import DirectRecipient, emit_event_notifications
from app.core.pii import (
    display_name_from_full_name,
    get_pii_crypto,
    mask_email,
    mask_full_name,
    mask_phone,
)
from app.models.crm import Counterparty, Product, Program
from app.models.deal import Deal, DealStageHistory
from app.models.service import ExternalLink, IntegrationEvent
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.integrations.schemas import InboundEnvelope, LeadBlock

logger = logging.getLogger("app.integrations")

INTG_SEEN_TTL_SECONDS = 7 * 24 * 3600

_LMS_EVENT_LABELS = {
    "lms.learning.started": "LMS: обучение начато",
    "lms.learning.progress": "LMS: обновлён прогресс обучения",
    "lms.learning.completed": "LMS: обучение завершено",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def find_seen_event(
    session: AsyncSession, event_id: uuid.UUID
) -> IntegrationEvent | None:
    return (
        await session.execute(
            select(IntegrationEvent).where(IntegrationEvent.event_id == event_id)
        )
    ).scalar_one_or_none()


async def mark_seen_fast(event_id: uuid.UUID) -> None:
    """KeyDB-заметка «событие видели» — ускоряет дедупликацию; истина в PG."""
    try:
        await get_cache().client.set(
            cache_key(f"intg:seen:{event_id}"), b"1", nx=True, ex=INTG_SEEN_TTL_SECONDS
        )
    except Exception:  # noqa: BLE001 — деградация без KeyDB штатная
        logger.debug("KeyDB недоступен на intg:seen — используем только PG-дедуп")


_LEAD_PII_MASKS = {"full_name": mask_full_name, "email": mask_email, "phone": mask_phone}


def mask_lead_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Маскирует ПДн блока `lead` (data-model.md §8.5) перед записью в JSONB.

    Открытые full_name/email/phone физлица живут только в `counterparty.*_enc`
    (шифртекст); в `integration_event.payload` и в журнале для observer —
    только маскированные значения («И***в И.И.», «i***v@example.com»).
    """
    lead = payload.get("lead")
    if not isinstance(lead, dict):
        return payload
    masked = dict(lead)
    for field, mask in _LEAD_PII_MASKS.items():
        value = masked.get(field)
        if isinstance(value, str) and value:
            masked[field] = mask(value)
    return {**payload, "lead": masked}


def _store_inbound(
    session: AsyncSession,
    envelope: InboundEnvelope,
    *,
    system: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
) -> IntegrationEvent:
    dumped = envelope.model_dump(mode="json")
    dumped["payload"] = mask_lead_payload(dumped["payload"])
    event = IntegrationEvent(
        event_id=envelope.event_id,
        direction="inbound",
        system=system,
        event_type=envelope.event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=dumped,
        status="processed",
        processed_at=_now(),
    )
    session.add(event)
    return event


async def _upsert_external_link(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    system: str,
    external_id: str,
) -> None:
    await session.execute(
        pg_insert(ExternalLink)
        .values(
            entity_type=entity_type, entity_id=entity_id, system=system,
            external_id=external_id,
        )
        .on_conflict_do_update(
            index_elements=[
                ExternalLink.entity_type, ExternalLink.entity_id, ExternalLink.system
            ],
            set_={"external_id": external_id},
        )
    )


# --------------------------------------------------------------------------------------
# LMS → CRM: lms.learning.*
# --------------------------------------------------------------------------------------


async def handle_lms_event(
    session: AsyncSession, envelope: InboundEnvelope
) -> dict[str, Any]:
    """Эффект в CRM (§13.3): запись в историю заявки + нотификация ответственному.

    Автоперехода по workflow нет — движение только людьми (открытое допущение №2
    контракта, `allowed_roles` не нарушаются).
    """
    if envelope.event_type not in LMS_INBOUND_EVENTS:
        raise unprocessable(f"Неизвестный тип события LMS: {envelope.event_type}")
    if envelope.correlation.crm_request_id is None:
        raise unprocessable("В correlation отсутствует crm_request_id")

    duplicate = await find_seen_event(session, envelope.event_id)
    if duplicate is not None:
        return {
            "accepted": True,
            "duplicate": True,
            "received_event_id": str(envelope.event_id),
        }

    deal = await session.get(Deal, envelope.correlation.crm_request_id)
    if deal is None:
        raise not_found("Заявка по crm_request_id не найдена")

    stage = await session.get(WorkflowStage, deal.stage_id)
    label = _LMS_EVENT_LABELS[envelope.event_type]
    status_block = envelope.payload.get("status") or {}
    detail = status_block.get("name")
    comment = f"{label}" + (f": {detail}" if detail and detail != label else "")

    if envelope.correlation.lms_enrollment_id:
        await _upsert_external_link(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system="lms",
            external_id=envelope.correlation.lms_enrollment_id,
        )
    # история без смены этапа: from == to, kind='integration' (снапшоты имён сохраняются)
    session.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=deal.stage_id,
            to_stage_id=deal.stage_id,
            from_stage_name=stage.name if stage else None,
            to_stage_name=stage.name if stage else "",
            actor_user_id=None,
            kind="integration",
            reason=envelope.event_type,
            comment=comment,
        )
    )
    deal.last_activity_at = _now()
    _store_inbound(session, envelope, system="lms", entity_type="deal", entity_id=deal.id)

    responsible = await session.get(AppUser, deal.responsible_user_id)
    if responsible is not None:
        await emit_event_notifications(
            session,
            event_type=envelope.event_type,
            entity_type="deal",
            entity_id=deal.id,
            subject=label,
            body=f"{label} по заявке «{deal.title}»",
            payload={
                "request_id": str(deal.id),
                "title": deal.title,
                "url": f"/requests/{deal.id}",
            },
            direct_recipients=[DirectRecipient(user=responsible)],
        )
    await session.commit()
    await mark_seen_fast(envelope.event_id)
    await get_cache().delete(f"deal:{deal.id}")
    return {
        "accepted": True,
        "duplicate": False,
        "received_event_id": str(envelope.event_id),
    }


# --------------------------------------------------------------------------------------
# CMS → CRM: cms.lead.created → B2C-заявка
# --------------------------------------------------------------------------------------


async def _find_or_create_counterparty(
    session: AsyncSession, lead: LeadBlock
) -> Counterparty:
    crypto = get_pii_crypto()
    if lead.client_kind == "person":
        if not lead.full_name or not lead.email:
            raise unprocessable("Для физлица в lead обязательны full_name и email")
        email_hmac = crypto.email_hmac(lead.email)
        existing = (
            await session.execute(
                select(Counterparty).where(Counterparty.email_hmac == email_hmac)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        counterparty = Counterparty(
            kind="person",
            display_name=display_name_from_full_name(lead.full_name),
            full_name=lead.full_name,
            email=lead.email,
            phone=lead.phone,
            email_hmac=email_hmac,
            full_name_hmac=crypto.full_name_hmac(lead.full_name),
        )
        session.add(counterparty)
        await session.flush()
        return counterparty

    if not lead.company_name:
        raise unprocessable("Для юрлица в lead обязателен company_name")
    stmt = select(Counterparty).where(Counterparty.kind == "legal_entity")
    stmt = (
        stmt.where(Counterparty.inn == lead.inn)
        if lead.inn
        else stmt.where(Counterparty.org_name == lead.company_name)
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing
    counterparty = Counterparty(
        kind="legal_entity",
        display_name=lead.company_name,
        org_name=lead.company_name,
        inn=lead.inn,
        org_email=lead.email,
        org_phone=lead.phone,
    )
    session.add(counterparty)
    await session.flush()
    return counterparty


async def _lead_responsible(session: AsyncSession) -> AppUser:
    for role in ("head_kam", "admin"):
        user = (
            (
                await session.execute(
                    select(AppUser)
                    .where(AppUser.roles.any(role), AppUser.is_active.is_(True))
                    .order_by(AppUser.created_at)
                )
            )
            .scalars()
            .first()
        )
        if user is not None:
            return user
    raise unprocessable("В системе нет активного head_kam/admin для приёма лида")


async def handle_cms_lead(
    session: AsyncSession, envelope: InboundEnvelope
) -> tuple[dict[str, Any], int]:
    """cms.lead.created → B2C-заявка в initial-статусе, source='cms' (§13.4).

    Возвращает (тело ответа, HTTP-статус): 201 — создана, 200 — duplicate.
    """
    if envelope.event_type != INTG_CMS_LEAD_CREATED:
        raise unprocessable(f"Неизвестный тип события CMS: {envelope.event_type}")
    duplicate = await find_seen_event(session, envelope.event_id)
    if duplicate is not None:
        return (
            {
                "accepted": True,
                "duplicate": True,
                "crm_request_id": str(duplicate.entity_id) if duplicate.entity_id else None,
            },
            200,
        )

    raw_lead = envelope.payload.get("lead")
    if not isinstance(raw_lead, dict):
        raise unprocessable("В payload отсутствует блок lead")
    lead = LeadBlock.model_validate(raw_lead)

    workflow = (
        await session.execute(select(Workflow).where(Workflow.code == "b2c"))
    ).scalar_one_or_none()
    if workflow is None:
        raise unprocessable("Воронка B2C не сконфигурирована")
    initial_stage = (
        await session.execute(
            select(WorkflowStage).where(
                WorkflowStage.workflow_id == workflow.id,
                WorkflowStage.is_initial.is_(True),
                WorkflowStage.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if initial_stage is None:
        raise unprocessable("У воронки B2C нет начального этапа")

    counterparty = await _find_or_create_counterparty(session, lead)
    responsible = await _lead_responsible(session)

    program_block = envelope.payload.get("program") or {}
    product_block = envelope.payload.get("product") or {}
    program = None
    product = None
    if program_block.get("crm_program_id"):
        try:
            program = await session.get(
                Program, uuid.UUID(str(program_block["crm_program_id"]))
            )
        except ValueError:
            program = None
    if product_block.get("crm_product_id"):
        try:
            product = await session.get(
                Product, uuid.UUID(str(product_block["crm_product_id"]))
            )
        except ValueError:
            product = None

    subject = program.name if program else "заявка с сайта"
    deal = Deal(
        pipeline="b2c",
        workflow_id=workflow.id,
        stage_id=initial_stage.id,
        title=f"{counterparty.display_name} — {subject}",
        counterparty_id=counterparty.id,
        product_id=product.id if product else (program.product_id if program else None),
        program_id=program.id if program else None,
        responsible_user_id=responsible.id,
        source="cms",
        description=lead.comment,
    )
    session.add(deal)
    await session.flush()
    session.add(
        DealStageHistory(
            deal_id=deal.id,
            from_stage_id=None,
            to_stage_id=initial_stage.id,
            from_stage_name=None,
            to_stage_name=initial_stage.name,
            actor_user_id=None,
            kind="integration",
            reason="cms_lead",
        )
    )
    if envelope.correlation.cms_lead_id:
        await _upsert_external_link(
            session,
            entity_type="deal",
            entity_id=deal.id,
            system="cms",
            external_id=envelope.correlation.cms_lead_id,
        )
    _store_inbound(session, envelope, system="cms", entity_type="deal", entity_id=deal.id)
    # ПДн лида в нотификацию не попадают — только display_name и программа
    await emit_event_notifications(
        session,
        event_type=EVENT_INTEGRATION_LEAD_RECEIVED,
        entity_type="deal",
        entity_id=deal.id,
        subject="Новый лид с сайта",
        body=f"Новая заявка с сайта: «{deal.title}» — требуется распределение",
        payload={
            "request_id": str(deal.id),
            "title": deal.title,
            "url": f"/requests/{deal.id}",
        },
        deal=deal,
    )
    await session.commit()
    await mark_seen_fast(envelope.event_id)
    return (
        {"accepted": True, "duplicate": False, "crm_request_id": str(deal.id)},
        201,
    )
