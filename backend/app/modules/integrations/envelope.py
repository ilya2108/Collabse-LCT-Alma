"""Сборка конверта IntegrationEvent (api-contract.md §13.1).

Состав payload зафиксирован кейсодержателем: статус, ответственный, вуз, программа,
продукт + ключи связи с БД и S3. Неприменимые блоки — null. ПДн клиентов/студентов
в конверт не попадают никогда (данные ответственного — рабочие контакты сотрудника).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm import Product, Program, University
from app.models.deal import Deal
from app.models.service import ExternalLink, FileObject
from app.models.user import AppUser
from app.models.workflow import WorkflowStage


async def _external_ids(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> dict[str, str]:
    rows = await session.execute(
        select(ExternalLink).where(
            ExternalLink.entity_type == entity_type, ExternalLink.entity_id == entity_id
        )
    )
    return {link.system: link.external_id for link in rows.scalars().all()}


async def _s3_keys_for_deal(session: AsyncSession, deal: Deal) -> list[str]:
    """Ключи S3 связанных документов: файлы заявки и скан договора."""
    conditions = [(FileObject.entity_type == "deal") & (FileObject.entity_id == deal.id)]
    if deal.contract_id is not None:
        conditions.append(
            (FileObject.entity_type == "contract") & (FileObject.entity_id == deal.contract_id)
        )
    clause = conditions[0]
    for extra in conditions[1:]:
        clause = clause | extra
    rows = await session.execute(
        select(FileObject.s3_key)
        .where(clause, FileObject.deleted_at.is_(None))
        .order_by(FileObject.created_at)
    )
    return [key for (key,) in rows.all()]


async def build_deal_envelope(
    session: AsyncSession,
    deal: Deal,
    *,
    event_type: str,
    stage: WorkflowStage,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Конверт события по заявке (например, `crm.request.handed_over`)."""
    responsible = await session.get(AppUser, deal.responsible_user_id)
    university = (
        await session.get(University, deal.university_id) if deal.university_id else None
    )
    program = await session.get(Program, deal.program_id) if deal.program_id else None
    product = await session.get(Product, deal.product_id) if deal.product_id else None

    deal_links = await _external_ids(session, "deal", deal.id)
    program_links = await _external_ids(session, "program", program.id) if program else {}

    db_keys: dict[str, Any] = {"request_id": str(deal.id)}
    if deal.contract_id:
        db_keys["contract_id"] = str(deal.contract_id)
    if deal.university_id:
        db_keys["university_id"] = str(deal.university_id)
    if deal.program_id:
        db_keys["program_id"] = str(deal.program_id)
    if deal.product_id:
        db_keys["product_id"] = str(deal.product_id)

    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "source": "crm",
        "correlation": {
            "crm_request_id": str(deal.id),
            "lms_enrollment_id": deal_links.get("lms"),
            "cms_lead_id": deal_links.get("cms"),
        },
        "payload": {
            "status": {"code": stage.code, "name": stage.name},
            "responsible": (
                {
                    "crm_user_id": str(responsible.id),
                    "full_name": responsible.full_name,
                    "email": responsible.email,
                }
                if responsible
                else None
            ),
            "university": (
                {
                    "crm_university_id": str(university.id),
                    "name": university.name,
                    "inn": university.inn,
                }
                if university
                else None
            ),
            "program": (
                {
                    "crm_program_id": str(program.id),
                    "name": program.name,
                    "lms_course_id": program_links.get("lms"),
                    "cms_external_id": program_links.get("cms"),
                }
                if program
                else None
            ),
            "product": (
                {"crm_product_id": str(product.id), "code": product.code, "name": product.name}
                if product
                else None
            ),
            "links": {
                "db_keys": db_keys,
                "s3_keys": await _s3_keys_for_deal(session, deal),
            },
        },
    }
