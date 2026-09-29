"""Доменные помощники модуля заявок: доступные переходы, контекст condition-DSL.

`GET /requests/{id}/transitions` — единственный источник действий для карточки и
канбана: RBAC-фильтр форсируется (недоступные роли не попадают в список), `condition`
только аннотирует (`available` + причина), решение всегда за человеком (Р-4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm import Counterparty, InteractionType
from app.models.deal import Deal
from app.models.user import AppUser
from app.models.workflow import WorkflowStage, WorkflowTransition
from app.modules.requests.serializers import client_kind_of
from app.modules.workflow.conditions import evaluate_condition
from app.modules.workflow.service import transition_role_allowed, user_can_write_deal


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

    Наружу не попадают рёбра, запрещённые RBAC (роль ∉ allowed_roles или нет права
    писать заявку); condition — аннотация, не фильтр. Закрытая заявка (терминал)
    действий не имеет — переоткрытие отдельным admin-эндпоинтом /reopen.
    """
    if deal.status != "open":
        return []
    if not user_can_write_deal(user.roles, user.id, deal.responsible_user_id):
        return []
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
        if not transition_role_allowed(user.roles, transition.allowed_roles):
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
