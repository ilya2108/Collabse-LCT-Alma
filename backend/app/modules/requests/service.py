"""Доменные помощники модуля заявок: доступные переходы, контекст condition-DSL,
клиент B2C-заявки.

`GET /requests/{id}/transitions` — единственный источник действий для карточки и
канбана: RBAC-фильтр форсируется (недоступные роли не попадают в список), `condition`
только аннотирует (`available` + причина), решение всегда за человеком (Р-4).

`find_or_create_counterparty` — общий поиск/создание клиента B2C для формы создания заявки
(`POST /requests` с `client`) и лида CMS (`cms.lead.created`): физлицо ищется по blind index
email, юрлицо — по ИНН, без ИНН — по точному названию; ПДн физлица шифруются типом
EncryptedStr, HMAC-колонки заполняются здесь.

`unassigned_condition` — правило «без ответственного КАМа» (заявка не распределена): одно
SQL-условие у фильтра `GET /requests?unassigned=true` и сигнала дашборда «Без
ответственного».
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.access import get_catalog_snapshot
from app.core.pii import (
    display_name_from_full_name,
    get_pii_crypto,
    mask_email,
    mask_full_name,
    mask_phone,
)
from app.core.rbac_catalog import WORKFLOW_TYPES
from app.models.crm import Counterparty, InteractionType
from app.models.deal import Deal
from app.models.user import AppUser
from app.models.workflow import WorkflowStage, WorkflowTransition
from app.modules.requests.serializers import client_kind_of
from app.modules.workflow.conditions import evaluate_condition
from app.modules.workflow.service import transition_role_allowed


async def find_or_create_counterparty(
    session: AsyncSession,
    *,
    client_kind: str,
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    company_name: str | None = None,
    inn: str | None = None,
) -> tuple[Counterparty, bool]:
    """Клиент B2C по данным формы или лида: (контрагент, создан ли он сейчас).

    `client_kind`: `person` — физлицо (нужен full_name), иначе юрлицо (нужен company_name);
    обязательность полей проверяет вызывающий код. Найденный клиент возвращается как есть —
    его карточка данными запроса не переписывается. Физлицо без email искать не по чему —
    создаётся новый контрагент. Только flush, commit — на вызывающей стороне.
    """
    if client_kind == "person":
        crypto = get_pii_crypto()
        email_hmac = crypto.email_hmac(email) if email else None
        if email_hmac is not None:
            existing = (
                await session.execute(
                    select(Counterparty).where(Counterparty.email_hmac == email_hmac)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
        if not full_name:
            raise ValueError("ФИО физлица проверяет вызывающий код")
        counterparty = Counterparty(
            kind="person",
            display_name=display_name_from_full_name(full_name),
            full_name=full_name,
            email=email,
            phone=phone,
            email_hmac=email_hmac,
            full_name_hmac=crypto.full_name_hmac(full_name),
        )
        session.add(counterparty)
        await session.flush()
        return counterparty, True

    if not company_name:
        raise ValueError("Название юрлица проверяет вызывающий код")
    stmt = select(Counterparty).where(Counterparty.kind == "legal_entity")
    stmt = (
        stmt.where(Counterparty.inn == inn)
        if inn
        else stmt.where(Counterparty.org_name == company_name)
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing, False
    counterparty = Counterparty(
        kind="legal_entity",
        display_name=company_name,
        org_name=company_name,
        inn=inn,
        org_email=email,
        org_phone=phone,
    )
    session.add(counterparty)
    await session.flush()
    return counterparty, True


def counterparty_audit_after(counterparty: Counterparty, *, source: str) -> dict[str, Any]:
    """Снимок нового клиента для audit_log: ПДн физлица — только маскированные
    (data-model.md §8.5), реквизиты юрлица — как есть."""
    if counterparty.kind == "person":
        return {
            "kind": "person",
            "source": source,
            "full_name": mask_full_name(counterparty.full_name or ""),
            "email": mask_email(counterparty.email) if counterparty.email else None,
            "phone": mask_phone(counterparty.phone) if counterparty.phone else None,
        }
    return {
        "kind": "legal_entity",
        "source": source,
        "org_name": counterparty.org_name,
        "inn": counterparty.inn,
    }


def unassigned_condition() -> sa.ColumnElement[bool]:
    """Заявка «без ответственного КАМа» — открыта и не распределена: её ответственный не
    ведёт портфель как КАМ процесса заявки (нет роли с правом `requests:kam` в этом
    процессе) либо деактивирован.

    `deal.responsible_user_id` в DDL NOT NULL, поэтому «пустого» ответственного не бывает:
    лид CMS до распределения висит на руководителе (`head_kam`), заявка может остаться на
    администраторе или на уволенном КАМе. Это то же правило, что «свободная заявка» в
    `POST /requests/{id}/assign` (её можно взять на себя), плюс деактивированный сотрудник.
    Роли — снимок каталога RBAC (своя роль «КАМ B2C» тоже КАМ); подзапрос по псевдониму
    `app_user`, чтобы не скоррелироваться с пользователем внешнего запроса.
    """
    catalog = get_catalog_snapshot()
    owner = aliased(AppUser, name="unassigned_owner")
    branches: list[sa.ColumnElement[bool]] = []
    for workflow_type in WORKFLOW_TYPES:
        roles = catalog.role_codes_with("requests:kam", workflow_type)
        if not roles:
            branches.append(Deal.pipeline == workflow_type)
            continue
        kam_owner = (
            select(owner.id)
            .where(
                owner.id == Deal.responsible_user_id,
                owner.is_active.is_(True),
                owner.roles.overlap(list(roles)),
            )
            .exists()
        )
        branches.append(sa.and_(Deal.pipeline == workflow_type, sa.not_(kam_owner)))
    return sa.and_(Deal.status == "open", sa.or_(*branches))


async def build_request_context(session: AsyncSession, deal: Deal) -> dict[str, Any]:
    """RequestContext для вычисления condition (workflow-engine.md §3.3)."""
    counterparty = (
        await session.get(Counterparty, deal.counterparty_id) if deal.counterparty_id else None
    )
    interaction = (
        await session.get(InteractionType, deal.interaction_type_id)
        if deal.interaction_type_id
        else None
    )
    return {
        "workflow_type": deal.pipeline,
        "client_kind": client_kind_of(counterparty),
        "university_id": str(deal.university_id) if deal.university_id else None,
        "amount": deal.amount,
        "interaction_type_code": interaction.code if interaction else None,
        "responsible_user_id": str(deal.responsible_user_id),
    }


async def available_transitions(
    session: AsyncSession, deal: Deal, user: AppUser
) -> list[dict[str, Any]]:
    """Рёбра из текущего этапа, доступные пользователю (api-contract.md §4.1, §9 движка).

    Наружу не попадают рёбра, запрещённые правами: нет `requests:transition` в процессе
    заявки (чужая заявка — ещё и `requests:write_any`) или ни одна из ролей пользователя,
    способных выполнять переходы в процессе, не входит в allowed_roles; condition —
    аннотация, не фильтр. Закрытая заявка (терминал) действий не имеет — переоткрытие
    отдельным эндпоинтом /reopen.
    """
    if deal.status != "open":
        return []
    access = user.access
    if not access.can_act_on_deal(
        "requests:transition",
        pipeline=deal.pipeline,
        responsible_user_id=deal.responsible_user_id,
        user_id=user.id,
    ):
        return []
    transition_roles = tuple(access.transition_roles(deal.pipeline))
    rows = (
        await session.execute(
            select(WorkflowTransition, WorkflowStage)
            .join(WorkflowStage, WorkflowStage.id == WorkflowTransition.to_stage_id)
            .where(
                WorkflowTransition.workflow_id == deal.workflow_id,
                WorkflowTransition.from_stage_id == deal.stage_id,
                WorkflowTransition.deleted_at.is_(None),
                WorkflowStage.deleted_at.is_(None),
            )
            .order_by(WorkflowTransition.sort_order)
        )
    ).all()
    if not rows:
        return []
    context = await build_request_context(session, deal)
    result: list[dict[str, Any]] = []
    for transition, to_stage in rows:
        if not transition_role_allowed(transition_roles, transition.allowed_roles):
            continue
        condition_ok = evaluate_condition(transition.condition, context)
        result.append(
            {
                "transition_id": str(transition.id),
                "to_status_id": str(to_stage.id),
                "to_status": {
                    "id": str(to_stage.id),
                    "code": to_stage.code,
                    "name": to_stage.name,
                    "color": to_stage.color,
                },
                "name": transition.name,
                "kind": "return" if transition.is_return else "forward",
                "requires_comment": transition.requires_comment,
                "available": condition_ok,
                "unavailable_reason": (
                    None if condition_ok else "Условие ветки не выполнено для этой заявки"
                ),
            }
        )
    return result
