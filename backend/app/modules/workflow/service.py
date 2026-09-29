"""Сервис движка workflow: переходы заявок, применение пакетов изменений, зависшие.

Алгоритмы — workflow-engine.md §6 (apply_workflow_change), §8 (check_stuck_requests),
§9.1 (transition_deal). Все бизнес-изменения пишут историю и outbox-события в одной
транзакции; инвалидация кэша и SSE — строго после коммита (data-model.md §10).
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.cache import cache_key, get_cache
from app.core.errors import ApiError, conflict, not_found, stale_version, unprocessable
from app.core.events import (
    EVENT_REQUEST_STUCK,
    EVENT_REQUEST_TRANSITIONED,
    EVENT_WORKFLOW_CHANGE_REQUESTED,
    INTG_CRM_REQUEST_HANDED_OVER,
    SSE_REQUEST_TRANSITIONED,
    SSE_WORKFLOW_CHANGED,
)
from app.core.outbox import (
    emit_event_notifications,
    emit_integration_event,
    stuck_dedup_key,
)
from app.core.sse import publish_event
from app.models.deal import Deal, DealStageHistory
from app.models.service import AppSetting, Notification, NotificationRule
from app.models.talent import Student, StudentStatusHistory
from app.models.user import AppUser
from app.models.workflow import (
    Workflow,
    WorkflowChangeRequest,
    WorkflowStage,
    WorkflowTransition,
)
from app.modules.integrations.envelope import build_deal_envelope
from app.modules.workflow.graph import (
    ApplyPlan,
    ConfirmNameMismatch,
    GraphStage,
    GraphTransition,
    OperationConflict,
    OperationUnprocessable,
    WorkflowGraph,
    apply_operations,
    default_migration_target,
    validate_graph,
    validate_migration,
    verify_confirm_names,
)
from app.modules.workflow.locks import (
    acquire_exclusive_workflow_lock,
    acquire_shared_workflow_lock,
)
from app.modules.workflow.schemas import Operation, is_dangerous

logger = logging.getLogger("app.workflow")

STUCK_DEFAULT_THRESHOLD_DAYS = 14
STUCK_DEFAULT_REPEAT_DAYS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Чистые guard-функции (юнит-тестируются без БД)
# --------------------------------------------------------------------------------------


def user_can_write_deal(
    roles: list[str] | tuple[str, ...], user_id: uuid.UUID, responsible_user_id: uuid.UUID
) -> bool:
    """RBAC §12.2: admin/head_kam пишут любую заявку, kam — только свою."""
    role_set = set(roles)
    if role_set & {"admin", "head_kam"}:
        return True
    if "kam" in role_set:
        return user_id == responsible_user_id
    return False


def transition_role_allowed(
    roles: list[str] | tuple[str, ...], allowed_roles: list[str] | None
) -> bool:
    """Пустой allowed_roles = достаточно доступа к заявке; иначе роль ∈ списку."""
    if not allowed_roles:
        return True
    return bool(set(roles) & set(allowed_roles))


def comment_satisfies(requires_comment: bool, comment: str | None) -> bool:
    if not requires_comment:
        return True
    return bool(comment and comment.strip())


def escalation_after_days(threshold_days: int) -> int:
    """Ступень 2 эскалации: 1.5×порога, округление вверх до дней (Р-6)."""
    return math.ceil(threshold_days * 1.5)


# --------------------------------------------------------------------------------------
# Загрузка живой схемы
# --------------------------------------------------------------------------------------


async def load_graph(session: AsyncSession, workflow_id: uuid.UUID) -> WorkflowGraph:
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
                select(WorkflowTransition).where(
                    WorkflowTransition.workflow_id == workflow_id,
                    WorkflowTransition.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    graph = WorkflowGraph(workflow_id=workflow_id)
    for row in stages:
        graph.stages[row.id] = GraphStage(
            id=row.id,
            code=row.code,
            name=row.name,
            sort_order=row.sort_order,
            is_initial=row.is_initial,
            is_terminal=row.is_terminal,
            terminal_outcome=row.terminal_outcome,
            stuck_threshold_days=row.stuck_threshold_days,
            triggers_lms_handover=row.triggers_lms_handover,
            color=row.color,
            description=row.description,
            ui_position=row.ui_position,
        )
    for row in transitions:
        graph.transitions[row.id] = GraphTransition(
            id=row.id,
            from_stage_id=row.from_stage_id,
            to_stage_id=row.to_stage_id,
            name=row.name,
            is_return=row.is_return,
            requires_comment=row.requires_comment,
            allowed_roles=list(row.allowed_roles or []),
            condition=row.condition,
            sort_order=row.sort_order,
        )
    return graph


async def invalidate_workflow_cache(workflow_id: uuid.UUID) -> None:
    await get_cache().delete(f"wf:{workflow_id}")


# --------------------------------------------------------------------------------------
# Переход заявки (workflow-engine.md §9.1)
# --------------------------------------------------------------------------------------


def _history_row(
    deal: Deal,
    *,
    from_stage: WorkflowStage | None,
    to_stage: WorkflowStage,
    transition: WorkflowTransition | None = None,
    kind: str = "manual",
    reason: str | None = None,
    actor_id: uuid.UUID | None = None,
    comment: str | None = None,
) -> DealStageHistory:
    return DealStageHistory(
        deal_id=deal.id,
        from_stage_id=from_stage.id if from_stage else None,
        to_stage_id=to_stage.id,
        from_stage_name=from_stage.name if from_stage else None,
        to_stage_name=to_stage.name,
        transition_id=transition.id if transition else None,
        is_return=bool(transition.is_return) if transition else False,
        actor_user_id=actor_id,
        kind=kind,
        reason=reason,
        comment=comment,
    )


def _transition_payload(deal: Deal, to_stage: WorkflowStage, **extra: Any) -> dict[str, Any]:
    payload = {
        "request_id": str(deal.id),
        "title": deal.title,
        "status": to_stage.name,
        "url": f"/requests/{deal.id}",
    }
    payload.update(extra)
    return payload


async def _talent_pool_hook_on_won(
    session: AsyncSession, deal: Deal, actor_id: uuid.UUID | None
) -> None:
    """B2C-заявка завершена (won): связанный студент двигается в graduate
    (workflow-engine.md §5.1 — точка интеграции с Talent Pool)."""
    if deal.pipeline != "b2c" or deal.counterparty_id is None:
        return
    student = (
        await session.execute(
            select(Student).where(Student.counterparty_id == deal.counterparty_id)
        )
    ).scalars().first()
    if student is None or student.funnel_status not in ("candidate", "studying"):
        return
    previous = student.funnel_status
    student.funnel_status = "graduate"
    student.status_changed_at = _now()
    session.add(
        StudentStatusHistory(
            student_id=student.id,
            from_status=previous,
            to_status="graduate",
            actor_user_id=actor_id,
            reason="b2c_request_completed",
        )
    )


async def transition_deal(
    session: AsyncSession,
    *,
    deal_id: uuid.UUID,
    to_status_id: uuid.UUID,
    actor: AppUser,
    comment: str | None,
    version: int,
    ip: str | None = None,
) -> Deal:
    """Единственная точка смены этапа по ребру живой схемы (инвариант I-4)."""
    # Порядок локов строго «advisory → row» — как в apply_change (exclusive advisory,
    # затем FOR UPDATE заявок мигрируемых этапов). Обратный порядок давал ABBA-deadlock:
    # T1 (переход) держит строку заявки и ждёт shared advisory, T2 (применение схемы)
    # держит exclusive advisory и ждёт строку той же заявки.
    workflow_id = (
        await session.execute(select(Deal.workflow_id).where(Deal.id == deal_id))
    ).scalar_one_or_none()
    if workflow_id is None:
        raise not_found("Заявка не найдена")
    # shared-лок workflow: переходы параллельны, но не пересекают применение схемы
    await acquire_shared_workflow_lock(session, workflow_id)

    deal = await session.get(Deal, deal_id, with_for_update=True)
    if deal is None or deal.status == "archived":
        raise not_found("Заявка не найдена")
    if deal.workflow_id != workflow_id:  # заявка воронку не меняет; страховка от гонки
        raise conflict("Схема заявки изменилась, повторите операцию")
    if deal.xmin != version:
        raise stale_version()

    # ВАЖНО: ребро перечитывается из ЖИВОЙ схемы после захвата локов —
    # она могла измениться, пока пользователь смотрел на карточку (409 → фронт перечитывает)
    transition = (
        await session.execute(
            select(WorkflowTransition).where(
                WorkflowTransition.workflow_id == deal.workflow_id,
                WorkflowTransition.from_stage_id == deal.stage_id,
                WorkflowTransition.to_stage_id == to_status_id,
                WorkflowTransition.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if transition is None:
        available = (
            (
                await session.execute(
                    select(WorkflowTransition.name)
                    .join(
                        WorkflowStage, WorkflowStage.id == WorkflowTransition.to_stage_id
                    )
                    .where(
                        WorkflowTransition.workflow_id == deal.workflow_id,
                        WorkflowTransition.from_stage_id == deal.stage_id,
                        WorkflowTransition.deleted_at.is_(None),
                        WorkflowStage.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        raise ApiError(
            409,
            "workflow_invalid_transition",
            "Переход не разрешён схемой workflow",
            details=[
                {
                    "field": "to_status_id",
                    "code": "workflow_invalid_transition",
                    "message": (
                        "Допустимые переходы: " + ", ".join(f"«{n}»" for n in available)
                        if available
                        else "Из текущего этапа переходов нет"
                    ),
                }
            ],
        )
    if not user_can_write_deal(actor.roles, actor.id, deal.responsible_user_id):
        raise ApiError(403, "forbidden", "Переход доступен только ответственному по заявке")
    if not transition_role_allowed(actor.roles, transition.allowed_roles):
        raise ApiError(403, "forbidden", "Ваша роль не может выполнить этот переход")
    if not comment_satisfies(transition.requires_comment, comment):
        message = (
            "Возврат на предыдущий этап требует комментарий с причиной"
            if transition.is_return
            else "Этот переход требует комментарий"
        )
        raise ApiError(
            400,
            "validation_error",
            message,
            details=[{"field": "comment", "code": "required", "message": message}],
        )

    from_stage = await session.get(WorkflowStage, deal.stage_id)
    to_stage = await session.get(WorkflowStage, transition.to_stage_id)
    if to_stage is None or to_stage.deleted_at is not None:
        raise conflict("Целевой этап был удалён, обновите схему")

    deal.stage_id = to_stage.id
    deal.stage_entered_at = _now()
    deal.last_activity_at = _now()
    if to_stage.is_terminal and to_stage.terminal_outcome:
        deal.status = to_stage.terminal_outcome
        if to_stage.terminal_outcome == "won":
            await _talent_pool_hook_on_won(session, deal, actor.id)
    elif deal.status != "open":
        deal.status = "open"  # выход возвратом из терминала

    session.add(
        _history_row(
            deal,
            from_stage=from_stage,
            to_stage=to_stage,
            transition=transition,
            kind="manual",
            actor_id=actor.id,
            comment=comment.strip() if comment else None,
        )
    )
    actor_label = actor.full_name or actor.username
    await emit_event_notifications(
        session,
        event_type=EVENT_REQUEST_TRANSITIONED,
        entity_type="deal",
        entity_id=deal.id,
        subject="Переход заявки",
        body=(
            f"Заявка «{deal.title}»: {from_stage.name if from_stage else '—'} → "
            f"{to_stage.name} ({actor_label})"
        ),
        payload=_transition_payload(
            deal,
            to_stage,
            from_status=from_stage.name if from_stage else None,
            comment=comment,
            is_return=transition.is_return,
        ),
        deal=deal,
        exclude_user_ids=(actor.id,),
    )
    if to_stage.triggers_lms_handover:
        envelope = await build_deal_envelope(
            session, deal, event_type=INTG_CRM_REQUEST_HANDED_OVER, stage=to_stage
        )
        await emit_integration_event(
            session,
            system="lms",
            event_type=INTG_CRM_REQUEST_HANDED_OVER,
            entity_type="deal",
            entity_id=deal.id,
            envelope=envelope,
        )
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="request.transitioned",
        entity_type="deal",
        entity_id=deal.id,
        before={"stage_id": str(from_stage.id) if from_stage else None},
        after={"stage_id": str(to_stage.id)},
        ip=ip,
    )
    await session.commit()

    await get_cache().delete(f"deal:{deal.id}")
    await publish_event(
        SSE_REQUEST_TRANSITIONED,
        {
            "request_id": str(deal.id),
            "workflow_id": str(deal.workflow_id),
            "from_status_id": str(from_stage.id) if from_stage else None,
            "to_status_id": str(to_stage.id),
            "actor_id": str(actor.id),
        },
    )
    return deal


async def reopen_deal(
    session: AsyncSession,
    *,
    deal_id: uuid.UUID,
    to_status_id: uuid.UUID,
    actor: AppUser,
    comment: str,
    ip: str | None = None,
) -> Deal:
    """Переоткрытие из терминала — единственное журналируемое исключение из
    «только по рёбрам» (admin-only, workflow-engine.md §3.1)."""
    # порядок локов «advisory → row» — единый с transition_deal/apply_change (анти-deadlock)
    workflow_id = (
        await session.execute(select(Deal.workflow_id).where(Deal.id == deal_id))
    ).scalar_one_or_none()
    if workflow_id is None:
        raise not_found("Заявка не найдена")
    await acquire_shared_workflow_lock(session, workflow_id)

    deal = await session.get(Deal, deal_id, with_for_update=True)
    if deal is None or deal.status == "archived":
        raise not_found("Заявка не найдена")
    if deal.workflow_id != workflow_id:  # заявка воронку не меняет; страховка от гонки
        raise conflict("Схема заявки изменилась, повторите операцию")
    current_stage = await session.get(WorkflowStage, deal.stage_id)
    if current_stage is None or not current_stage.is_terminal:
        raise conflict("Переоткрыть можно только заявку в терминальном статусе")
    target = await session.get(WorkflowStage, to_status_id)
    if (
        target is None
        or target.deleted_at is not None
        or target.workflow_id != deal.workflow_id
    ):
        raise unprocessable("Целевой этап не найден в воронке заявки")
    if target.is_terminal:
        raise unprocessable("Переоткрытие в терминальный этап невозможно")

    deal.stage_id = target.id
    deal.status = "open"
    deal.stage_entered_at = _now()
    deal.last_activity_at = _now()
    session.add(
        _history_row(
            deal,
            from_stage=current_stage,
            to_stage=target,
            kind="manual",
            reason="reopen",
            actor_id=actor.id,
            comment=comment.strip(),
        )
    )
    await emit_event_notifications(
        session,
        event_type=EVENT_REQUEST_TRANSITIONED,
        entity_type="deal",
        entity_id=deal.id,
        subject="Заявка переоткрыта",
        body=(
            f"Заявка «{deal.title}» переоткрыта администратором: "
            f"{current_stage.name} → {target.name}"
        ),
        payload=_transition_payload(deal, target, reopen=True, comment=comment),
        deal=deal,
        exclude_user_ids=(actor.id,),
    )
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="request.reopened",
        entity_type="deal",
        entity_id=deal.id,
        before={"stage_id": str(current_stage.id), "status": current_stage.terminal_outcome},
        after={"stage_id": str(target.id), "status": "open"},
        ip=ip,
    )
    await session.commit()

    await get_cache().delete(f"deal:{deal.id}")
    await publish_event(
        SSE_REQUEST_TRANSITIONED,
        {
            "request_id": str(deal.id),
            "workflow_id": str(deal.workflow_id),
            "from_status_id": str(current_stage.id),
            "to_status_id": str(target.id),
            "actor_id": str(actor.id),
            "reopen": True,
        },
    )
    return deal


# --------------------------------------------------------------------------------------
# Пакеты изменений схемы (workflow-engine.md §6)
# --------------------------------------------------------------------------------------


async def _open_deal_counts(
    session: AsyncSession, stage_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not stage_ids:
        return {}
    rows = await session.execute(
        select(Deal.stage_id, func.count())
        .where(Deal.stage_id.in_(stage_ids), Deal.status == "open")
        .group_by(Deal.stage_id)
    )
    return dict(rows.all())


def _wrap_graph_errors(exc: Exception) -> ApiError:
    if isinstance(exc, OperationConflict):
        return conflict(str(exc))
    if isinstance(exc, (OperationUnprocessable, ConfirmNameMismatch)):
        return unprocessable(str(exc))
    raise exc


def _invalid_schema_error(errors: list[dict[str, Any]]) -> ApiError:
    return ApiError(
        400,
        "workflow_invalid_schema",
        "Схема workflow после применения операций невалидна",
        details=errors,
    )


def _invalid_migration_error(errors: list[dict[str, Any]]) -> ApiError:
    # семантические ошибки миграции (V-8) — 422 unprocessable (api-contract.md §1.4:
    # «target-статус миграции = удаляемый» и т.п.)
    return ApiError(
        422,
        "unprocessable",
        "Перенос заявок удаляемых этапов настроен некорректно",
        details=errors,
    )


async def compute_impact(
    session: AsyncSession, live: WorkflowGraph, operations: list[Operation]
) -> tuple[dict[str, Any], ApplyPlan]:
    """Валидация пакета против живой схемы + расчёт impact (api-contract.md §5.3)."""
    try:
        target, plan = apply_operations(live, operations)
    except (OperationConflict, OperationUnprocessable) as exc:
        raise _wrap_graph_errors(exc) from exc
    counts = await _open_deal_counts(session, [i.stage_id for i in plan.deleted_stages])
    graph_errors = validate_graph(target)
    if graph_errors:
        raise _invalid_schema_error(graph_errors)
    migration_errors = validate_migration(live, plan, counts)
    if migration_errors:
        raise _invalid_migration_error(migration_errors)

    by_status = []
    total = 0
    for item in plan.deleted_stages:
        count = counts.get(item.stage_id, 0)
        total += count
        migrate_to: dict[str, Any] | None = None
        if item.migrate_to_status_id is not None:
            migrate_to = {"id": str(item.migrate_to_status_id), "name": item.migrate_to_name}
        else:
            suggested = default_migration_target(live, item.stage_id)
            if suggested is not None:
                migrate_to = {"id": str(suggested.id), "name": suggested.name}
        by_status.append(
            {
                "status_id": str(item.stage_id),
                "status_name": item.stage_name,
                "count": count,
                "migrate_to": migrate_to,
            }
        )
    return {"affected_requests_total": total, "by_status": by_status}, plan


async def _execute_plan(
    session: AsyncSession,
    wcr: WorkflowChangeRequest,
    actor: AppUser,
    live: WorkflowGraph,
    operations: list[Operation],
) -> dict[str, Any]:
    """Мутация схемы + миграция заявок по плану. Вызывается ВНУТРИ транзакции
    с уже взятым эксклюзивным advisory-локом workflow. Возвращает migration_report."""
    try:
        verify_confirm_names(live, operations)
        target, plan = apply_operations(live, operations)
    except (OperationConflict, OperationUnprocessable, ConfirmNameMismatch) as exc:
        raise _wrap_graph_errors(exc) from exc
    counts = await _open_deal_counts(session, [i.stage_id for i in plan.deleted_stages])
    graph_errors = validate_graph(target)
    if graph_errors:
        raise _invalid_schema_error(graph_errors)
    migration_errors = validate_migration(live, plan, counts)
    if migration_errors:
        raise _invalid_migration_error(migration_errors)

    workflow = await session.get(Workflow, wcr.workflow_id)
    stage_rows: dict[uuid.UUID, WorkflowStage] = {
        row.id: row
        for row in (
            await session.execute(
                select(WorkflowStage).where(
                    WorkflowStage.workflow_id == wcr.workflow_id,
                    WorkflowStage.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    transition_rows: dict[uuid.UUID, WorkflowTransition] = {
        row.id: row
        for row in (
            await session.execute(
                select(WorkflowTransition).where(
                    WorkflowTransition.workflow_id == wcr.workflow_id,
                    WorkflowTransition.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }

    # 1. новые этапы (UUID берутся из плана — граф и БД согласованы)
    for stage in plan.new_stages:
        row = WorkflowStage(
            id=stage.id,
            workflow_id=wcr.workflow_id,
            code=stage.code,
            name=stage.name,
            description=stage.description,
            sort_order=stage.sort_order,
            is_initial=False,
            is_terminal=stage.is_terminal,
            terminal_outcome=stage.terminal_outcome,
            stuck_threshold_days=stage.stuck_threshold_days,
            triggers_lms_handover=stage.triggers_lms_handover,
            color=stage.color,
            ui_position=stage.ui_position,
        )
        session.add(row)
        stage_rows[row.id] = row
    # 2. правки и переименования этапов
    for stage_id, patch in plan.stage_patches:
        row = stage_rows[stage_id]
        for attribute, value in patch.items():
            setattr(row, attribute, value)
    for stage_id, _old_name, new_name in plan.renames:
        stage_rows[stage_id].name = new_name
    # 3. рёбра: новые, правки, удаления
    for transition in plan.new_transitions:
        session.add(
            WorkflowTransition(
                id=transition.id,
                workflow_id=wcr.workflow_id,
                from_stage_id=transition.from_stage_id,
                to_stage_id=transition.to_stage_id,
                name=transition.name,
                is_return=transition.is_return,
                requires_comment=transition.requires_comment,
                allowed_roles=transition.allowed_roles,
                condition=transition.condition,
                sort_order=transition.sort_order,
            )
        )
    for transition_id, patch in plan.transition_patches:
        row = transition_rows[transition_id]
        for attribute, value in patch.items():
            setattr(row, attribute, value)
    for transition_id in plan.deleted_transition_ids:
        transition_rows[transition_id].deleted_at = _now()
    await session.flush()

    # 4. миграция заявок удаляемых этапов, затем мягкое удаление этапов и их рёбер
    migrated_total = 0
    report_by_status: list[dict[str, Any]] = []
    for item in plan.deleted_stages:
        target_row = stage_rows.get(item.migrate_to_status_id) \
            if item.migrate_to_status_id else None
        deals = (
            (
                await session.execute(
                    select(Deal)
                    .where(Deal.stage_id == item.stage_id, Deal.status == "open")
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for deal in deals:
            assert target_row is not None  # гарантировано V-8 (E_MAPPING_REQUIRED)
            deal.stage_id = target_row.id
            # stage_entered_at НЕ трогаем: перестройка схемы не «омолаживает» заявку
            deal.last_activity_at = _now()
            session.add(
                _history_row(
                    deal,
                    from_stage=stage_rows[item.stage_id],
                    to_stage=target_row,
                    kind="system_migration",
                    reason="stage_deleted",
                    actor_id=None,
                    comment=(
                        f"Статус «{item.stage_name}» удалён администратором, "
                        f"заявка перенесена в «{target_row.name}»"
                    ),
                )
            )
            await emit_event_notifications(
                session,
                event_type=EVENT_REQUEST_TRANSITIONED,
                entity_type="deal",
                entity_id=deal.id,
                subject="Заявка перенесена",
                body=(
                    f"Статус «{item.stage_name}» удалён администратором, заявка "
                    f"«{deal.title}» перенесена в «{target_row.name}»"
                ),
                payload=_transition_payload(
                    deal, target_row, migrated=True, from_status=item.stage_name
                ),
                deal=deal,
            )
            migrated_total += 1
        if deals:
            report_by_status.append(
                {
                    "from": item.stage_name,
                    "to": target_row.name if target_row else "",
                    "count": len(deals),
                }
            )
        for transition_id in item.deleted_transition_ids:
            transition_rows[transition_id].deleted_at = _now()
        stage_rows[item.stage_id].deleted_at = _now()

    if workflow is not None:
        workflow.updated_by = actor.id
        workflow.updated_at = _now()

    wcr.status = "applied"
    wcr.decided_by = actor.id
    wcr.decided_at = _now()
    wcr.applied_at = _now()

    await write_audit(
        session,
        actor_user_id=actor.id,
        action="workflow.change_applied",
        entity_type="workflow",
        entity_id=wcr.workflow_id,
        before=None,
        after={
            "change_request_id": str(wcr.id),
            "operations": wcr.operations,
            "migrated_requests": migrated_total,
        },
    )
    await session.flush()
    return {"migrated_requests": migrated_total, "by_status": report_by_status}


async def _after_apply_effects(workflow_id: uuid.UUID, change_id: uuid.UUID) -> None:
    await invalidate_workflow_cache(workflow_id)
    await publish_event(
        SSE_WORKFLOW_CHANGED, {"workflow_id": str(workflow_id), "change_id": str(change_id)}
    )


async def create_change(
    session: AsyncSession,
    *,
    workflow_id: uuid.UUID,
    author: AppUser,
    operations: list[Operation],
    raw_operations: list[dict[str, Any]],
    comment: str | None,
    ip: str | None = None,
) -> WorkflowChangeRequest:
    """Создание пакета операций (api-contract.md §5.3).

    Классификация: пакет с опасной операцией — всегда pending (даже от admin,
    двухшаговое подтверждение); безопасный от admin — применяется сразу;
    от head_kam — pending на апрув.
    """
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Воронка не найдена")
    live = await load_graph(session, workflow_id)
    try:
        verify_confirm_names(live, operations)  # рубеж 1 «защиты от дурака»
    except (OperationConflict, ConfirmNameMismatch) as exc:
        raise _wrap_graph_errors(exc) from exc
    impact, _plan = await compute_impact(session, live, operations)

    wcr = WorkflowChangeRequest(
        workflow_id=workflow_id,
        operations=raw_operations,
        impact=impact,
        status="pending",
        requested_by=author.id,
        decision_comment=comment,
    )
    session.add(wcr)
    await session.flush()

    dangerous = any(is_dangerous(op) for op in operations)
    apply_now = not dangerous and "admin" in author.roles

    if apply_now:
        await acquire_exclusive_workflow_lock(session, workflow_id)
        # схему никто не менял с момента чтения выше только под локом — перечитываем
        live = await load_graph(session, workflow_id)
        await _execute_plan(session, wcr, author, live, operations)
        await session.commit()
        await _after_apply_effects(workflow_id, wcr.id)
        return wcr

    await write_audit(
        session,
        actor_user_id=author.id,
        action="workflow.change_requested",
        entity_type="workflow",
        entity_id=workflow_id,
        after={"change_request_id": str(wcr.id), "operations": raw_operations},
        ip=ip,
    )
    await emit_event_notifications(
        session,
        event_type=EVENT_WORKFLOW_CHANGE_REQUESTED,
        entity_type="workflow_change_request",
        entity_id=wcr.id,
        subject="Изменение схемы ждёт согласования",
        body=(
            f"{author.full_name or author.username} предложил(а) изменение схемы "
            f"«{workflow.name}» — требуется решение администратора"
        ),
        payload={
            "workflow_id": str(workflow_id),
            "change_request_id": str(wcr.id),
            "url": "/admin/approvals",
        },
    )
    await session.commit()
    return wcr


async def apply_change(
    session: AsyncSession, *, change_id: uuid.UUID, approver: AppUser
) -> tuple[WorkflowChangeRequest, dict[str, Any]]:
    """Approve = атомарное применение одной транзакцией (workflow-engine.md §6.4).

    Ошибка применения откатывает всё (заявки не тронуты), пакет помечается `failed`
    отдельной транзакцией — автор пересоздаёт (state-машина data-model.md §5.2).
    """
    wcr = await session.get(WorkflowChangeRequest, change_id, with_for_update=True)
    if wcr is None:
        raise not_found("Пакет изменений не найден")
    if wcr.status != "pending":
        raise conflict("Пакет уже обработан")

    # эксклюзивный лок: дожидаемся хвоста переходов, замораживаем новые (§6.5)
    await acquire_exclusive_workflow_lock(session, wcr.workflow_id)
    live = await load_graph(session, wcr.workflow_id)
    try:
        operations = parse_operations(wcr.operations)
        report = await _execute_plan(session, wcr, approver, live, operations)
    except ApiError as exc:
        await session.rollback()
        await _mark_change_failed(session, change_id, approver, exc.message)
        raise
    await session.commit()
    await _after_apply_effects(wcr.workflow_id, wcr.id)
    return wcr, report


async def _mark_change_failed(
    session: AsyncSession, change_id: uuid.UUID, approver: AppUser, error: str
) -> None:
    wcr = await session.get(WorkflowChangeRequest, change_id, with_for_update=True)
    if wcr is None or wcr.status != "pending":
        return
    wcr.status = "failed"
    wcr.decided_by = approver.id
    wcr.decided_at = _now()
    wcr.decision_comment = f"Ошибка применения: {error}"[:2000]
    await session.commit()


async def reject_change(
    session: AsyncSession, *, change_id: uuid.UUID, approver: AppUser, reason: str
) -> WorkflowChangeRequest:
    wcr = await session.get(WorkflowChangeRequest, change_id, with_for_update=True)
    if wcr is None:
        raise not_found("Пакет изменений не найден")
    if wcr.status != "pending":
        raise conflict("Пакет уже обработан")
    wcr.status = "rejected"
    wcr.decided_by = approver.id
    wcr.decided_at = _now()
    wcr.decision_comment = reason
    await write_audit(
        session,
        actor_user_id=approver.id,
        action="workflow.change_rejected",
        entity_type="workflow_change_request",
        entity_id=wcr.id,
        after={"reason": reason},
    )
    await session.commit()
    return wcr


def parse_operations(raw_operations: list[dict[str, Any]]) -> list[Operation]:
    """Валидация JSONB-пакета в Pydantic-модели (при применении сохранённого пакета)."""
    adapter: TypeAdapter[list[Operation]] = TypeAdapter(list[Operation])
    try:
        return adapter.validate_python(raw_operations)
    except ValidationError as exc:
        raise unprocessable(f"Пакет операций повреждён: {exc.errors()[0].get('msg', '')}") from exc


# --------------------------------------------------------------------------------------
# «Зависшие» заявки (workflow-engine.md §8)
# --------------------------------------------------------------------------------------


async def _global_stuck_default(session: AsyncSession) -> int:
    setting = await session.get(AppSetting, "stuck_threshold_days_default")
    if setting and isinstance(setting.value, dict):
        value = setting.value.get("value")
        if isinstance(value, int) and 1 <= value <= 60:
            return value
    return STUCK_DEFAULT_THRESHOLD_DAYS


def _pick_stuck_rule(
    rules: list[NotificationRule], deal: Deal
) -> NotificationRule | None:
    """Наиболее специфичное правило: stage > workflow > глобальное."""
    matching = [
        r
        for r in rules
        if (r.workflow_id is None or r.workflow_id == deal.workflow_id)
        and (r.stage_id is None or r.stage_id == deal.stage_id)
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda r: (r.stage_id is not None, r.workflow_id is not None),
    )


def effective_stuck_threshold(
    rule: NotificationRule | None,
    stage_threshold: int | None,
    workflow_threshold: int | None,
    global_default: int,
) -> int:
    """Каскад порога (Р-5): rule.params.threshold_days → stage → workflow → app_setting."""
    if rule is not None:
        value = (rule.params or {}).get("threshold_days")
        if isinstance(value, int) and 1 <= value <= 60:
            return value
    if stage_threshold is not None:
        return stage_threshold
    if workflow_threshold is not None:
        return workflow_threshold
    return global_default


def _repeat_every_days(rule: NotificationRule | None) -> int:
    if rule is not None:
        value = (rule.params or {}).get("repeat_every_days")
        if isinstance(value, int) and value >= 1:
            return value
    return STUCK_DEFAULT_REPEAT_DAYS


async def _stuck_guard_acquired(deal_id: uuid.UUID, ttl_days: int) -> bool:
    """KeyDB-guard `crm:v1:stuck:guard:{deal_id}` — быстрый путь дедупликации между
    репликами; истина всегда в `notification.dedup_key` (PG)."""
    try:
        acquired = await get_cache().client.set(
            cache_key(f"stuck:guard:{deal_id}"), b"1", nx=True, ex=ttl_days * 86400
        )
        return bool(acquired)
    except Exception:  # noqa: BLE001 — недоступный KeyDB не отключает проверку
        return True


async def check_stuck_requests(session: AsyncSession) -> dict[str, Any]:
    """Идемпотентная проверка зависших: безопасна к параллельному запуску
    (`FOR UPDATE SKIP LOCKED` + уникальный dedup_key)."""
    started = time.monotonic()
    enabled_setting = await session.get(AppSetting, "stuck_check_enabled")
    if enabled_setting and isinstance(enabled_setting.value, dict):
        if enabled_setting.value.get("value") is False:
            return {
                "checked": 0,
                "stuck_found": 0,
                "newly_stuck": 0,
                "notifications_sent": 0,
                "escalations_sent": 0,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "disabled": True,
            }
    global_default = await _global_stuck_default(session)
    escalate_setting = await session.get(AppSetting, "stuck_escalate_to_manager")
    escalate_enabled = True
    if escalate_setting and isinstance(escalate_setting.value, dict):
        escalate_enabled = escalate_setting.value.get("value") is not False
    rules = list(
        (
            await session.execute(
                select(NotificationRule).where(
                    NotificationRule.event_type == EVENT_REQUEST_STUCK,
                    NotificationRule.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    responsible_rules = [r for r in rules if r.recipient_type == "responsible"]
    manager_rules = [r for r in rules if r.recipient_type == "manager_of_responsible"]

    checked = (
        await session.execute(
            select(func.count())
            .select_from(Deal)
            .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
            .where(Deal.status == "open", WorkflowStage.is_terminal.is_(False))
        )
    ).scalar_one()

    candidates = (
        (
            await session.execute(
                select(Deal, WorkflowStage, Workflow)
                .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
                .join(Workflow, Workflow.id == Deal.workflow_id)
                .where(
                    Deal.status == "open",
                    WorkflowStage.is_terminal.is_(False),
                    func.now() - Deal.stage_entered_at
                    > func.make_interval(
                        0, 0, 0,
                        func.coalesce(
                            WorkflowStage.stuck_threshold_days,
                            Workflow.stuck_threshold_days,
                            global_default,
                        ),
                    ),
                )
                .with_for_update(of=Deal, skip_locked=True)
            )
        ).all()
    )

    stuck_found = len(candidates)
    newly_stuck = 0
    notifications_sent = 0
    escalations_sent = 0
    now = _now()

    for deal, stage, workflow in candidates:
        days = max((now - deal.stage_entered_at).days, 0)
        resp_rule = _pick_stuck_rule(responsible_rules, deal)
        mgr_rule = _pick_stuck_rule(manager_rules, deal)
        threshold = effective_stuck_threshold(
            resp_rule, stage.stuck_threshold_days, workflow.stuck_threshold_days, global_default
        )
        repeat_days = _repeat_every_days(resp_rule)
        if days <= threshold:
            continue  # порог правила строже каскада SQL — заявка ещё не зависла

        if not await _stuck_guard_acquired(deal.id, repeat_days):
            continue

        prior = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.dedup_key.like(f"stuck:{deal.id}:{deal.stage_id}:%"))
            )
        ).scalar_one()
        if prior == 0:
            newly_stuck += 1

        payload = {
            "request_id": str(deal.id),
            "title": deal.title,
            "status": stage.name,
            "stuck_days": days,
            "threshold_days": threshold,
            "url": f"/requests/{deal.id}",
        }
        created = await emit_event_notifications(
            session,
            event_type=EVENT_REQUEST_STUCK,
            entity_type="deal",
            entity_id=deal.id,
            subject="Заявка зависла",
            body=(
                f"🔥 Заявка «{deal.title}» висит на этапе «{stage.name}» {days} дн. "
                f"(порог {threshold} дн.)"
            ),
            payload=payload,
            deal=deal,
            recipient_types=("responsible",),
            dedup_key=stuck_dedup_key(
                deal.id, deal.stage_id, "responsible", days, repeat_days
            ),
        )
        notifications_sent += created

        mgr_threshold = effective_stuck_threshold(
            mgr_rule, stage.stuck_threshold_days, workflow.stuck_threshold_days, global_default
        )
        if escalate_enabled and days >= escalation_after_days(mgr_threshold):
            mgr_repeat = _repeat_every_days(mgr_rule)
            created = await emit_event_notifications(
                session,
                event_type=EVENT_REQUEST_STUCK,
                entity_type="deal",
                entity_id=deal.id,
                subject="Эскалация: заявка зависла",
                body=(
                    f"⚠️ Эскалация: заявка «{deal.title}» вашего сотрудника висит на этапе "
                    f"«{stage.name}» уже {days} дн. (порог {mgr_threshold} дн.)"
                ),
                payload={**payload, "escalation": True},
                deal=deal,
                recipient_types=("manager_of_responsible",),
                dedup_key=stuck_dedup_key(
                    deal.id, deal.stage_id, "manager", days, mgr_repeat
                ),
            )
            escalations_sent += created

    await session.commit()
    return {
        "checked": checked,
        "stuck_found": stuck_found,
        "newly_stuck": newly_stuck,
        "notifications_sent": notifications_sent,
        "escalations_sent": escalations_sent,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
