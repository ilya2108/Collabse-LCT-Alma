"""Сериализация заявок в формат API (api-contract.md §4.2–4.4).

Мост терминологии: deal → Request, stage → status, stage_entered_at → status_updated_at.
ПДн клиента-физлица (B2C) для роли observer маскируются (§4.2); данные шифруются
на уровне хранения (EncryptedStr), здесь — только представление.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import mask_email, mask_full_name, mask_phone
from app.models.crm import Counterparty
from app.models.deal import Deal, DealComment, DealStageHistory
from app.models.service import AuditLog, ExternalLink
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.requests.schemas import (
    ClientOut,
    CommentOut,
    ExternalRefs,
    RequestOut,
    StatusRef,
    UserRef,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stuck_info(deal: Deal, stage: WorkflowStage, workflow: Workflow) -> tuple[bool, int]:
    """(is_stuck, stuck_days) по каскаду порога stage → workflow (Р-5).

    Закрытые заявки и терминальные этапы не «зависают» — SLA-таймер не считается.
    """
    if deal.status != "open" or stage.is_terminal:
        return False, 0
    days = max((_now() - deal.stage_entered_at).days, 0)
    threshold = (
        stage.stuck_threshold_days
        if stage.stuck_threshold_days is not None
        else workflow.stuck_threshold_days
    )
    return days > threshold, days


def client_kind_of(counterparty: Counterparty | None) -> str | None:
    if counterparty is None:
        return None
    return "person" if counterparty.kind == "person" else "company"


def serialize_client(counterparty: Counterparty | None, observer: bool) -> ClientOut | None:
    if counterparty is None:
        return None
    if counterparty.kind == "person":
        full_name = counterparty.full_name
        email = counterparty.email
        phone = counterparty.phone
        if observer:
            full_name = mask_full_name(full_name) if full_name else None
            email = mask_email(email) if email else None
            phone = mask_phone(phone) if phone else None
        return ClientOut(full_name=full_name, email=email, phone=phone)
    return ClientOut(
        full_name=None,
        email=counterparty.org_email,
        phone=counterparty.org_phone,
        company_name=counterparty.org_name,
        inn=counterparty.inn,
    )


async def load_external_refs(session: AsyncSession, deal_id: uuid.UUID) -> ExternalRefs:
    rows = await session.execute(
        select(ExternalLink).where(
            ExternalLink.entity_type == "deal", ExternalLink.entity_id == deal_id
        )
    )
    refs = {link.system: link.external_id for link in rows.scalars().all()}
    return ExternalRefs(cms_lead_id=refs.get("cms"), lms_enrollment_id=refs.get("lms"))


async def serialize_deal(
    session: AsyncSession,
    deal: Deal,
    *,
    viewer: AppUser,
    stage: WorkflowStage | None = None,
    workflow: Workflow | None = None,
    available_transitions: list[dict[str, Any]] | None = None,
    last_comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stage = stage or await session.get(WorkflowStage, deal.stage_id)
    workflow = workflow or await session.get(Workflow, deal.workflow_id)
    assignee = await session.get(AppUser, deal.responsible_user_id)
    counterparty = (
        await session.get(Counterparty, deal.counterparty_id) if deal.counterparty_id else None
    )
    is_observer = "observer" in (viewer.roles or [])
    is_stuck, stuck_days = stuck_info(deal, stage, workflow)
    out = RequestOut(
        id=deal.id,
        workflow_type=deal.pipeline,
        workflow_id=deal.workflow_id,
        title=deal.title,
        university_id=deal.university_id,
        client_kind=client_kind_of(counterparty),
        counterparty_id=deal.counterparty_id,
        interaction_type_id=deal.interaction_type_id,
        client=serialize_client(counterparty, is_observer),
        contract_id=deal.contract_id,
        product_id=deal.product_id,
        program_id=deal.program_id,
        status=StatusRef(id=stage.id, code=stage.code, name=stage.name, color=stage.color),
        request_status=deal.status,
        assignee=UserRef(id=assignee.id, full_name=assignee.full_name) if assignee else None,
        source=deal.source,
        external_refs=await load_external_refs(session, deal.id),
        amount=format(deal.amount, "f") if deal.amount is not None else None,
        currency=deal.currency,
        status_updated_at=deal.stage_entered_at,
        last_activity_at=deal.last_activity_at,
        is_stuck=is_stuck,
        stuck_days=stuck_days,
        description=deal.description,
        version=deal.xmin,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
        available_transitions=available_transitions,
        last_comments=last_comments,
    )
    return out.model_dump(mode="json", exclude_none=False)


def serialize_board_card(
    deal: Deal,
    stage: WorkflowStage,
    workflow: Workflow,
    assignee: AppUser | None,
    client_display: str | None,
) -> dict[str, Any]:
    is_stuck, stuck_days = stuck_info(deal, stage, workflow)
    return {
        "id": str(deal.id),
        "title": deal.title,
        "client": client_display,
        "assignee": (
            {"id": str(assignee.id), "full_name": assignee.full_name} if assignee else None
        ),
        "amount": format(deal.amount, "f") if deal.amount is not None else None,
        "currency": deal.currency,
        "status_updated_at": deal.stage_entered_at.isoformat(),
        "is_stuck": is_stuck,
        "stuck_days": stuck_days,
        "version": deal.xmin,
    }


def serialize_comment(comment: DealComment, author: AppUser | None) -> dict[str, Any]:
    deleted = comment.deleted_at is not None
    return CommentOut(
        id=comment.id,
        text="(удалено)" if deleted else comment.body,
        author=UserRef(
            id=author.id if author else comment.author_user_id,
            full_name=author.full_name if author else None,
        ),
        created_at=comment.created_at,
        deleted=deleted,
    ).model_dump(mode="json")


def _history_kind(row: DealStageHistory) -> str:
    if row.from_stage_id is None:
        return "created"
    if row.kind == "system_migration":
        return "migrated"
    return "transition"


def serialize_history_row(
    row: DealStageHistory,
    stages: dict[uuid.UUID, WorkflowStage],
    users: dict[uuid.UUID, AppUser],
) -> dict[str, Any]:
    """Элемент истории (§4.3): имена этапов — снапшоты, коды — из строк этапов
    (включая soft-deleted, история читаема вечно — I-11)."""

    def status_block(stage_id: uuid.UUID | None, snapshot_name: str | None):
        if stage_id is None:
            return None
        stage = stages.get(stage_id)
        return {
            "id": str(stage_id),
            "code": stage.code if stage else None,
            "name": snapshot_name or (stage.name if stage else None),
        }

    actor = users.get(row.actor_user_id) if row.actor_user_id else None
    return {
        "id": str(row.id),
        "kind": _history_kind(row),
        "from_status": status_block(row.from_stage_id, row.from_stage_name),
        "to_status": status_block(row.to_stage_id, row.to_stage_name),
        "is_return": row.is_return,
        "reason": row.reason,
        "comment": row.comment,
        "actor": (
            {"id": str(actor.id), "full_name": actor.full_name} if actor else None
        ),
        "created_at": row.created_at.isoformat(),
    }


def serialize_audit_history_item(
    row: AuditLog, users: dict[uuid.UUID, AppUser]
) -> dict[str, Any] | None:
    """Подмешивание assign/field_change из audit_log в таймлайн (§3.5 движка)."""
    kind_map = {"request.assigned": "assign", "request.field_changed": "field_change"}
    kind = kind_map.get(row.action)
    if kind is None:
        return None
    actor = users.get(row.actor_user_id) if row.actor_user_id else None
    return {
        "id": f"audit-{row.id}",
        "kind": kind,
        "from_status": None,
        "to_status": None,
        "is_return": False,
        "reason": None,
        "comment": None,
        "changes": {"before": row.before, "after": row.after},
        "actor": (
            {"id": str(actor.id), "full_name": actor.full_name} if actor else None
        ),
        "created_at": row.created_at.isoformat(),
    }
