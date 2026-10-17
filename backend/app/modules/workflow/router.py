"""Workflow: схемы для конструктора/канбана и пакеты изменений с апрувом.

Контракт — api-contract.md §5; семантика движка — workflow-engine.md §6.
GET /workflows/{id} — cache-aside `crm:v1:wf:{id}` (TTL 24 ч, DEL при применении CR).

Права (Р-F2-7) — с областью по процессу, проверяются в процессе воронки (`wf.code`):
- схема процесса видна при `workflow:read` (конструктор) или `requests:read` (доска и
  карточка заявки строятся по схеме); список — только такие процессы, чужая схема — 404;
- `workflow:change` — предпросмотр и пакет изменений; `workflow:history` — история
  пакетов; `workflow:approve` — согласование и отклонение (и «безопасный пакет автора
  применяется сразу»). Право есть, но не в процессе воронки — 403; пакет процесса, схема
  которого пользователю не видна, — 404.
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
from app.core.errors import forbidden, not_found
from app.core.pagination import ListParams, list_envelope, list_params
from app.core.rbac_catalog import WORKFLOW_TYPE_LABELS, WORKFLOW_TYPES, permission_name
from app.core.security import get_current_user, require_permission
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
    MigrationReport,
    MigrationReportByStatus,
    MigrationTimings,
    RejectBody,
    UserRef,
)
from app.modules.workflow.serializers import serialize_workflow

router = APIRouter(prefix="/workflows", tags=["workflow"])

WORKFLOW_CACHE_TTL = 24 * 3600

# права, при которых схема процесса видна (конструктор / доска и карточки заявок)
SCHEMA_READ_PERMISSIONS = ("workflow:read", "requests:read")


def visible_workflow_types(user: AppUser) -> tuple[str, ...]:
    """Процессы, схему которых пользователь видит."""
    access = user.access
    return tuple(
        wf for wf in WORKFLOW_TYPES if access.has_any(*SCHEMA_READ_PERMISSIONS, workflow_type=wf)
    )


def ensure_workflow_permission(user: AppUser, workflow_code: str, permission: str) -> None:
    """403, если права нет в процессе воронки (а не «хоть в одном процессе»)."""
    if not user.access.has(permission, workflow_code):
        label = WORKFLOW_TYPE_LABELS.get(workflow_code, workflow_code)
        raise forbidden(f"Недостаточно прав в процессе {label}: «{permission_name(permission)}»")


async def _workflow_or_404(session: AsyncSession, workflow_id: uuid.UUID) -> Workflow:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Воронка не найдена")
    return workflow


async def _change_workflow_code(
    session: AsyncSession, change_id: uuid.UUID, user: AppUser, permission: str
) -> str:
    """Процесс пакета изменений с проверкой права в нём. Только колонки, без загрузки
    пакета в сессию: approve дальше берёт строку пакета FOR UPDATE и сверяет статус —
    объект из identity map со старым статусом сломал бы защиту от двойного применения."""
    code = (
        await session.execute(
            select(Workflow.code)
            .join(WorkflowChangeRequest, WorkflowChangeRequest.workflow_id == Workflow.id)
            .where(WorkflowChangeRequest.id == change_id)
        )
    ).scalar_one_or_none()
    if code is None or code not in visible_workflow_types(user):
        raise not_found("Пакет изменений не найден")
    ensure_workflow_permission(user, code, permission)
    return code


@router.get("", summary="Список воронок")
async def list_workflows(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    visible = visible_workflow_types(user)
    if not visible:
        return []
    workflows = (
        (
            await session.execute(
                select(Workflow).where(Workflow.code.in_(visible)).order_by(Workflow.code)
            )
        )
        .scalars()
        .all()
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
                select(func.count()).where(Deal.workflow_id == wf.id, Deal.status == "open")
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
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    # схема процесса вне области — 404, как несуществующая (Р-F2-10)
    visible = visible_workflow_types(user)
    cached = await get_cache().get_json(f"wf:{workflow_id}")
    if cached is not None:
        if cached.get("type") not in visible:
            raise not_found("Воронка не найдена")
        return cached
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None or workflow.code not in visible:
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


def _migration_report_out(report: dict[str, Any]) -> MigrationReport:
    """Отчёт переноса (хранится в `impact['migration_report']`, `by_status` — from/to)."""
    timings = report.get("timings")
    return MigrationReport(
        migrated_requests=int(report.get("migrated_requests") or 0),
        by_status=[
            MigrationReportByStatus(
                from_status=row["from"], to_status=row["to"], count=row["count"]
            )
            for row in report.get("by_status") or []
        ],
        digest_notifications=report.get("digest_notifications"),
        timings=(
            MigrationTimings.model_validate(timings)
            if isinstance(timings, dict) and "apply_ms" in timings
            else None
        ),
    )


def _serialize_change(
    wcr: WorkflowChangeRequest,
    author: AppUser | None,
    decided_by: AppUser | None,
) -> dict[str, Any]:
    # отчёт переноса живёт в impact (JSONB, без миграции БД), наружу — отдельным полем;
    # impact отдаётся прежней формы ImpactOut
    impact = dict(wcr.impact) if isinstance(wcr.impact, dict) else wcr.impact
    report = impact.pop("migration_report", None) if isinstance(impact, dict) else None
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
        impact=impact,
        migration_report=_migration_report_out(report) if isinstance(report, dict) else None,
        created_at=wcr.requested_at,
        decided_at=wcr.decided_at,
        applied_at=wcr.applied_at,
    ).model_dump(mode="json", by_alias=True)


async def _load_change_users(
    session: AsyncSession, wcr: WorkflowChangeRequest
) -> tuple[AppUser | None, AppUser | None]:
    author = await session.get(AppUser, wcr.requested_by)
    decided = await session.get(AppUser, wcr.decided_by) if wcr.decided_by else None
    return author, decided


@router.post("/{workflow_id}/impact-preview", summary="Предпросмотр влияния операций")
async def impact_preview(
    workflow_id: uuid.UUID,
    body: ImpactPreviewBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("workflow:change"))],
) -> dict[str, Any]:
    workflow = await _workflow_or_404(session, workflow_id)
    ensure_workflow_permission(user, workflow.code, "workflow:change")
    live = await service.load_graph(session, workflow_id)
    impact, _plan = await service.compute_impact(
        session, live, body.operations, workflow_code=workflow.code
    )
    return {"impact": ImpactOut.model_validate(impact).model_dump(mode="json")}


@router.post(
    "/{workflow_id}/changes",
    status_code=201,
    summary="Создать пакет операций изменения схемы",
    description=(
        "Право «Правка схемы: черновик и пакет изменений» в процессе воронки. Безопасный "
        "пакет автора с правом «Согласование и публикация изменений схемы» в этом процессе "
        "применяется сразу (status=applied), иначе — на согласование (pending)."
    ),
)
async def create_change(
    workflow_id: uuid.UUID,
    body: ChangeCreateBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("workflow:change"))],
    request: Request,
) -> dict[str, Any]:
    workflow = await _workflow_or_404(session, workflow_id)
    ensure_workflow_permission(user, workflow.code, "workflow:change")
    raw_operations = [op.model_dump(mode="json", exclude_none=True) for op in body.operations]
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


@router.get("/{workflow_id}/changes", summary="Список изменений схемы")
async def list_changes(
    workflow_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("workflow:history"))],
    params: Annotated[ListParams, Depends(list_params)],
    status: Annotated[
        Literal["pending", "approved", "rejected", "applied", "failed"] | None, Query()
    ] = None,
) -> dict[str, Any]:
    workflow = await _workflow_or_404(session, workflow_id)
    ensure_workflow_permission(user, workflow.code, "workflow:history")
    stmt = select(WorkflowChangeRequest).where(WorkflowChangeRequest.workflow_id == workflow_id)
    if status:
        stmt = stmt.where(WorkflowChangeRequest.status == status)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
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


@router.get("/changes/{change_id}", summary="Карточка изменения схемы")
async def get_change(
    change_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("workflow:history"))],
) -> dict[str, Any]:
    await _change_workflow_code(session, change_id, user, "workflow:history")
    wcr = await session.get(WorkflowChangeRequest, change_id)
    if wcr is None:
        raise not_found("Пакет изменений не найден")
    author, decided = await _load_change_users(session, wcr)
    return _serialize_change(wcr, author, decided)


@router.post(
    "/changes/{change_id}/approve",
    summary="Одобрить и атомарно применить изменение",
    description=(
        "Право «Согласование и публикация изменений схемы» в процессе воронки пакета. "
        "Одна транзакция: схема, перенос заявок удаляемых этапов, история, сводные "
        "уведомления и аудит. 409 `conflict` — пакет уже обработан; 409 `workflow_busy` "
        "(заголовок Retry-After) — воронка занята переходами, пакет остаётся на "
        "согласовании; 400 `workflow_invalid_schema`, 422 `unprocessable` — пакет "
        "помечен failed, заявки не тронуты."
    ),
)
async def approve_change(
    change_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("workflow:approve"))],
) -> dict[str, Any]:
    await _change_workflow_code(session, change_id, user, "workflow:approve")
    wcr, report = await service.apply_change(session, change_id=change_id, approver=user)
    return ApplyResultOut(
        id=wcr.id,
        status=wcr.status,
        approved_by=UserRef(id=user.id, full_name=user.full_name),
        applied_at=wcr.applied_at,
        migration_report=_migration_report_out(report),
    ).model_dump(mode="json", by_alias=True)


@router.post(
    "/changes/{change_id}/reject",
    summary="Отклонить изменение",
    description="Право «Согласование и публикация изменений схемы» в процессе воронки пакета.",
)
async def reject_change(
    change_id: uuid.UUID,
    body: RejectBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("workflow:approve"))],
) -> dict[str, Any]:
    await _change_workflow_code(session, change_id, user, "workflow:approve")
    wcr = await service.reject_change(
        session, change_id=change_id, approver=user, reason=body.reason
    )
    author, decided = await _load_change_users(session, wcr)
    return _serialize_change(wcr, author, decided)
