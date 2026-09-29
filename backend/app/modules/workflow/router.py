"""Workflow: схемы для конструктора/канбана и пакеты изменений с апрувом.

Контракт — api-contract.md §5; семантика движка — workflow-engine.md §6.
GET /workflows/{id} — cache-aside `crm:v1:wf:{id}` (TTL 24 ч, DEL при применении CR).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip
from app.core.cache import get_cache
from app.core.db import get_session
from app.core.errors import not_found
from app.core.pagination import ListParams, list_envelope, list_params
from app.core.security import get_current_user, require_roles
from app.models.deal import Deal
from app.models.user import AppUser
from app.models.workflow import (
    Workflow,
    WorkflowChangeRequest,
    WorkflowStage,
    WorkflowTransition,
)
from app.modules.workflow import service
from app.modules.workflow.schemas import (
    ApplyResultOut,
    ChangeCreateBody,
    ChangeRequestOut,
    ImpactOut,
    ImpactPreviewBody,
    RejectBody,
    UserRef,
)
from app.modules.workflow.serializers import serialize_workflow

router = APIRouter(prefix="/workflows", tags=["workflow"])

WORKFLOW_CACHE_TTL = 24 * 3600


@router.get("", summary="Список воронок")
async def list_workflows(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    workflows = (
        (await session.execute(select(Workflow).order_by(Workflow.code))).scalars().all()
    )
    result: list[dict[str, Any]] = []
    for wf in workflows:
        statuses_count = (
            await session.execute(
                select(func.count()).where(
                    WorkflowStage.workflow_id == wf.id, WorkflowStage.deleted_at.is_(None)
                )
            )
        ).scalar_one()
        active_requests = (
            await session.execute(
                select(func.count()).where(
                    Deal.workflow_id == wf.id, Deal.status == "open"
                )
            )
        ).scalar_one()
        result.append(
            {
                "id": str(wf.id),
                "type": wf.code,
                "name": wf.name,
                "statuses_count": statuses_count,
                "active_requests": active_requests,
                "updated_at": wf.updated_at.isoformat(),
            }
        )
    return result


@router.get("/{workflow_id}", summary="Полная схема воронки (конструктор и канбан)")
async def get_workflow(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    cached = await get_cache().get_json(f"wf:{workflow_id}")
    if cached is not None:
        return cached
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Воронка не найдена")
    stages = (
        (
            await session.execute(
                select(WorkflowStage).where(
                    WorkflowStage.workflow_id == workflow_id,
                    WorkflowStage.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    transitions = (
        (
            await session.execute(
                select(WorkflowTransition)
                .where(
                    WorkflowTransition.workflow_id == workflow_id,
                    WorkflowTransition.deleted_at.is_(None),
                )
                .order_by(WorkflowTransition.sort_order)
            )
        )
        .scalars()
        .all()
    )
    payload = serialize_workflow(workflow, list(stages), list(transitions))
    await get_cache().set_json(f"wf:{workflow_id}", payload, WORKFLOW_CACHE_TTL)
    return payload


# --- конструктор изменений (пакеты операций, апрув, миграция) -----------------------------


def _serialize_change(
    wcr: WorkflowChangeRequest,
    author: AppUser | None,
    decided_by: AppUser | None,
) -> dict[str, Any]:
    return ChangeRequestOut(
        id=wcr.id,
        workflow_id=wcr.workflow_id,
        status=wcr.status,
        author=UserRef(id=author.id, full_name=author.full_name) if author else None,
        decided_by=(
            UserRef(id=decided_by.id, full_name=decided_by.full_name) if decided_by else None
        ),
        decision_comment=wcr.decision_comment,
        operations=list(wcr.operations or []),
        impact=wcr.impact,
        created_at=wcr.requested_at,
        decided_at=wcr.decided_at,
        applied_at=wcr.applied_at,
    ).model_dump(mode="json")


async def _load_change_users(
    session: AsyncSession, wcr: WorkflowChangeRequest
) -> tuple[AppUser | None, AppUser | None]:
    author = await session.get(AppUser, wcr.requested_by)
    decided = await session.get(AppUser, wcr.decided_by) if wcr.decided_by else None
    return author, decided


@router.post(
    "/{workflow_id}/impact-preview",
    summary="Предпросмотр влияния операций",
    dependencies=[Depends(require_roles("admin", "head_kam"))],
)
async def impact_preview(
    workflow_id: uuid.UUID,
    body: ImpactPreviewBody,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Воронка не найдена")
    live = await service.load_graph(session, workflow_id)
    impact, _plan = await service.compute_impact(session, live, body.operations)
    return {"impact": ImpactOut.model_validate(impact).model_dump(mode="json")}


@router.post(
    "/{workflow_id}/changes",
    status_code=201,
    summary="Создать пакет операций изменения схемы",
)
async def create_change(
    workflow_id: uuid.UUID,
    body: ChangeCreateBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam"))],
    request: Request,
) -> dict[str, Any]:
    raw_operations = [
        op.model_dump(mode="json", exclude_none=True) for op in body.operations
    ]
    wcr = await service.create_change(
        session,
        workflow_id=workflow_id,
        author=user,
        operations=body.operations,
        raw_operations=raw_operations,
        comment=body.comment,
        ip=client_ip(request),
    )
    author, decided = await _load_change_users(session, wcr)
    return _serialize_change(wcr, author, decided)


@router.get(
    "/{workflow_id}/changes",
    summary="Список изменений схемы",
    dependencies=[Depends(require_roles("admin", "head_kam", "observer"))],
)
async def list_changes(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    params: Annotated[ListParams, Depends(list_params)],
    status: Annotated[
        Literal["pending", "approved", "rejected", "applied", "failed"] | None, Query()
    ] = None,
) -> dict[str, Any]:
    if await session.get(Workflow, workflow_id) is None:
        raise not_found("Воронка не найдена")
    stmt = select(WorkflowChangeRequest).where(
        WorkflowChangeRequest.workflow_id == workflow_id
    )
    if status:
        stmt = stmt.where(WorkflowChangeRequest.status == status)
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                stmt.order_by(WorkflowChangeRequest.requested_at.desc())
                .limit(params.limit)
                .offset(params.offset)
            )
        )
        .scalars()
        .all()
    )
    items = []
    for wcr in rows:
        author, decided = await _load_change_users(session, wcr)
        items.append(_serialize_change(wcr, author, decided))
    return list_envelope(items, total, params)


@router.get(
    "/changes/{change_id}",
    summary="Карточка изменения схемы",
    dependencies=[Depends(require_roles("admin", "head_kam", "observer"))],
)
async def get_change(
    change_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    wcr = await session.get(WorkflowChangeRequest, change_id)
    if wcr is None:
        raise not_found("Пакет изменений не найден")
    author, decided = await _load_change_users(session, wcr)
    return _serialize_change(wcr, author, decided)


@router.post(
    "/changes/{change_id}/approve",
    summary="Одобрить и атомарно применить изменение (только admin)",
)
async def approve_change(
    change_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    wcr, report = await service.apply_change(session, change_id=change_id, approver=user)
    return ApplyResultOut(
        id=wcr.id,
        status=wcr.status,
        approved_by=UserRef(id=user.id, full_name=user.full_name),
        applied_at=wcr.applied_at,
        migration_report={
            "migrated_requests": report["migrated_requests"],
            "by_status": [
                {"from_status": row["from"], "to_status": row["to"], "count": row["count"]}
                for row in report["by_status"]
            ],
        },
    ).model_dump(mode="json", by_alias=True)


@router.post(
    "/changes/{change_id}/reject",
    summary="Отклонить изменение (только admin)",
)
async def reject_change(
    change_id: uuid.UUID,
    body: RejectBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    wcr = await service.reject_change(
        session, change_id=change_id, approver=user, reason=body.reason
    )
    author, decided = await _load_change_users(session, wcr)
    return _serialize_change(wcr, author, decided)
