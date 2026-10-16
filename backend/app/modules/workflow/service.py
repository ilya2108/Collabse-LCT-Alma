"""Сервис движка workflow: переходы заявок, применение пакетов изменений, зависшие.

Алгоритмы — workflow-engine.md §6 (apply_workflow_change), §8 (check_stuck_requests),
§9.1 (transition_deal). Все бизнес-изменения пишут историю и outbox-события в одной
транзакции; инвалидация кэша и SSE — строго после коммита (data-model.md §10).

Права (F2, Р-F2-7, Р-F2-12): переход заявки — `requests:transition` в процессе заявки
(чужой — ещё `requests:write_any`) и пересечение ролей пользователя, способных выполнять
переходы в процессе, с `allowed_roles` ребра. Роли в `allowed_roles` — коды динамического
каталога: при применении пакета наборы ролей читаются свежим SELECT ПОСЛЕ эксклюзивного
лока воронки (`transition_capable_roles`, `known_role_codes`) — удаление роли
(`access.service.delete_role`) берёт те же локи, гонки «пакет добавил роль, которую в этот
момент удалили» нет. Безопасный пакет применяется сразу, если у автора есть
`workflow:approve` в процессе воронки.
"""

from __future__ import annotations

import copy
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.access import compute_access, static_catalog
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
from app.core.rbac_catalog import WORKFLOW_TYPE_LABELS
from app.core.sse import publish_event
from app.models.access import AppRole, AppRolePermission
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
from app.modules.workflow import migration
from app.modules.workflow.graph import (
    ApplyPlan,
    ConfirmNameMismatch,
    GraphStage,
    GraphTransition,
    OperationConflict,
    OperationUnprocessable,
    WorkflowGraph,
    apply_operations,
    check_operation_roles,
    default_migration_target,
    validate_graph,
    validate_migration,
    verify_confirm_names,
)
from app.modules.workflow.locks import (
    acquire_exclusive_workflow_lock,
    acquire_shared_workflow_lock,
    lock_timeout_as_busy,
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
    roles: list[str] | tuple[str, ...],
    user_id: uuid.UUID,
    responsible_user_id: uuid.UUID,
    *,
    pipeline: str = "b2b",
) -> bool:
    """Совместимость (тесты): правка заявки по кодам СИСТЕМНЫХ ролей — статический
    каталог, `requests:write` в процессе и (`requests:write_any` или своя заявка).
    Для 4 базовых ролей — прежнее правило §12.2: admin/head_kam пишут любую, kam — свою.
    Новый код вызывает `user.access.can_act_on_deal(...)` (свои роли из каталога БД)."""
    return compute_access(roles, static_catalog()).can_act_on_deal(
        "requests:write",
        pipeline=pipeline,
        responsible_user_id=responsible_user_id,
        user_id=user_id,
    )


def transition_role_allowed(
    roles: list[str] | tuple[str, ...] | frozenset[str], allowed_roles: list[str] | None
) -> bool:
    """Пустой allowed_roles = достаточно доступа к заявке; иначе роль ∈ списку.

    `roles` — роли пользователя, способные выполнять переходы в процессе заявки
    (`user.access.transition_roles(pipeline)`)."""
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
        (
            await session.execute(
                select(Student).where(Student.counterparty_id == deal.counterparty_id)
            )
        )
        .scalars()
        .first()
    )
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
    # shared-лок workflow: переходы параллельны, но не пересекают применение схемы.
    # Ожидание ограничено lock_timeout (locks.TRANSITION_LOCK_TIMEOUT_MS, до конца
    # транзакции — включая FOR UPDATE строки): дольше — 409 workflow_busy + Retry-After
    async with lock_timeout_as_busy():
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
                    .join(WorkflowStage, WorkflowStage.id == WorkflowTransition.to_stage_id)
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
    access = actor.access
    if not access.has("requests:transition", deal.pipeline):
        raise ApiError(
            403,
            "forbidden",
            f"Перевод заявок процесса {WORKFLOW_TYPE_LABELS.get(deal.pipeline, deal.pipeline)} "
            "недоступен вашей роли",
        )
    if not access.can_act_on_deal(
        "requests:transition",
        pipeline=deal.pipeline,
        responsible_user_id=deal.responsible_user_id,
        user_id=actor.id,
    ):
        raise ApiError(403, "forbidden", "Переход доступен только ответственному по заявке")
    if not transition_role_allowed(
        access.transition_roles(deal.pipeline), transition.allowed_roles
    ):
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
    async with lock_timeout_as_busy():  # ожидание ограничено — как у transition_deal
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
    if target is None or target.deleted_at is not None or target.workflow_id != deal.workflow_id:
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
    session: AsyncSession, workflow_id: uuid.UUID, stage_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    # предикат workflow_id — запрос целиком покрывается ix_deal_board
    # (workflow_id, stage_id) WHERE status = 'open'
    if not stage_ids:
        return {}
    rows = await session.execute(
        select(Deal.stage_id, func.count())
        .where(
            Deal.workflow_id == workflow_id,
            Deal.stage_id.in_(stage_ids),
            Deal.status == "open",
        )
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


async def transition_capable_roles(session: AsyncSession, workflow_code: str) -> frozenset[str]:
    """Коды ролей, которые могут выполнять переходы в процессе: право
    `requests:transition` и процесс в области роли (строгий V-7).

    Свежий SELECT, а не кэш каталога процесса: `_execute_plan` вызывает его под
    эксклюзивным локом воронки, и роль, удалённая параллельно, уже не попадёт в переходы.
    """
    rows = await session.execute(
        select(AppRole.code)
        .join(AppRolePermission, AppRolePermission.role_id == AppRole.id)
        .where(
            AppRolePermission.permission_code == "requests:transition",
            AppRole.workflow_types.any(workflow_code),
        )
    )
    return frozenset(rows.scalars().all())


async def known_role_codes(session: AsyncSession) -> frozenset[str]:
    """Коды всех ролей каталога (мягкий V-7 живых переходов) — свежим SELECT."""
    return frozenset((await session.execute(select(AppRole.code))).scalars().all())


async def _workflow_code(session: AsyncSession, workflow_id: uuid.UUID) -> str:
    code = (
        await session.execute(select(Workflow.code).where(Workflow.id == workflow_id))
    ).scalar_one_or_none()
    if code is None:
        raise not_found("Воронка не найдена")
    return code


async def _validate_target(
    session: AsyncSession,
    *,
    workflow_code: str,
    live: WorkflowGraph,
    target: WorkflowGraph,
    operations: list[Operation],
) -> list[dict[str, Any]]:
    """V-1..V-7 целевой схемы: строгий V-7 для ролей, которые добавляет пакет, и мягкий —
    для всех переходов (роль, уже отклонённая строгой проверкой, не дублируется)."""
    capable = await transition_capable_roles(session, workflow_code)
    known = await known_role_codes(session)
    role_errors = check_operation_roles(operations, capable, live=live)
    flagged = {error["role"] for error in role_errors}
    return role_errors + validate_graph(target, known_roles=known | flagged)


async def compute_impact(
    session: AsyncSession,
    live: WorkflowGraph,
    operations: list[Operation],
    *,
    workflow_code: str | None = None,
) -> tuple[dict[str, Any], ApplyPlan]:
    """Валидация пакета против живой схемы + расчёт impact (api-contract.md §5.3).

    Роли переходов — строгий и мягкий V-7 по текущему каталогу (без лока: предпросмотр и
    создание пакета; окончательная проверка — в `_execute_plan` под локом воронки).
    """
    try:
        target, plan = apply_operations(live, operations)
    except (OperationConflict, OperationUnprocessable) as exc:
        raise _wrap_graph_errors(exc) from exc
    if workflow_code is None:
        workflow_code = await _workflow_code(session, live.workflow_id)
    counts = await _open_deal_counts(
        session, live.workflow_id, [i.stage_id for i in plan.deleted_stages]
    )
    graph_errors = await _validate_target(
        session, workflow_code=workflow_code, live=live, target=target, operations=operations
    )
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


APPLY_BUSY_MESSAGE = (
    "По воронке сейчас проходят переходы заявок — пакет не применён и остаётся "
    "на согласовании, повторите через несколько секунд"
)
INTERNAL_APPLY_ERROR = "внутренняя ошибка сервера — изменения отменены, заявки не перенесены"


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


async def _execute_plan(
    session: AsyncSession,
    wcr: WorkflowChangeRequest,
    actor: AppUser,
    live: WorkflowGraph,
    operations: list[Operation],
) -> dict[str, Any]:
    """Мутация схемы + перенос заявок по плану. Вызывается ВНУТРИ транзакции
    с уже взятым эксклюзивным advisory-локом workflow. Возвращает migration_report
    (он же сохраняется в `wcr.impact['migration_report']`).

    Заявки удаляемых этапов переносятся пакетно (`migration.migrate_deleted_stages` —
    один оператор на этап), получатели узнают о переносе сводкой
    (`migration.emit_migration_digests`). Вызов — через атрибуты модуля `migration`:
    инструмент замера подменяет перенос поштучной legacy-версией (tools/bench_migration).
    """
    try:
        verify_confirm_names(live, operations)
        target, plan = apply_operations(live, operations)
    except (OperationConflict, OperationUnprocessable, ConfirmNameMismatch) as exc:
        raise _wrap_graph_errors(exc) from exc
    counts = await _open_deal_counts(
        session, wcr.workflow_id, [i.stage_id for i in plan.deleted_stages]
    )
    workflow = await session.get(Workflow, wcr.workflow_id)
    if workflow is None:
        raise not_found("Воронка не найдена")
    # Роли переходов — свежим SELECT уже ПОД эксклюзивным локом воронки, а не из кэша
    # каталога: роль, удалённая до захвата лока, даёт E_UNKNOWN_ROLE; удаление роли после
    # COMMIT применения увидит её в переходах и вернёт 409 role_in_use (те же локи)
    graph_errors = await _validate_target(
        session, workflow_code=workflow.code, live=live, target=target, operations=operations
    )
    if graph_errors:
        raise _invalid_schema_error(graph_errors)
    migration_errors = validate_migration(live, plan, counts)
    if migration_errors:
        raise _invalid_migration_error(migration_errors)

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
    # 3. рёбра: новые, правки, удаления (новое ребро к этапу, удаляемому этим же
    #    пакетом, тоже попадает в transition_rows — его пометит шаг 4)
    for transition in plan.new_transitions:
        transition_row = WorkflowTransition(
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
        session.add(transition_row)
        transition_rows[transition_row.id] = transition_row
    for transition_id, patch in plan.transition_patches:
        row = transition_rows[transition_id]
        for attribute, value in patch.items():
            setattr(row, attribute, value)
    for transition_id in plan.deleted_transition_ids:
        transition_rows[transition_id].deleted_at = _now()
    await session.flush()

    # 4. перенос заявок удаляемых этапов — один оператор на этап (CTE UPDATE … RETURNING
    #    → строки истории), затем мягкое удаление этапов и их рёбер: сначала перенос,
    #    потом deleted_at (I-2 — открытых заявок на удалённом этапе не бывает)
    started = time.perf_counter()
    outcome = await migration.migrate_deleted_stages(
        session, workflow_id=wcr.workflow_id, plan=plan, stage_rows=stage_rows
    )
    migrate_ms = _ms(time.perf_counter() - started)
    deleted_at = _now()
    for item in plan.deleted_stages:
        for transition_id in item.deleted_transition_ids:
            transition_rows[transition_id].deleted_at = deleted_at
        stage_rows[item.stage_id].deleted_at = deleted_at

    # 5. сводные уведомления — одно на получателя вместо одного на заявку (§6.3 п.4);
    #    в этой же транзакции: нет переноса — нет уведомления
    digests = await migration.emit_migration_digests(
        session, outcome=outcome, workflow=workflow, change_id=wcr.id
    )
    workflow.updated_by = actor.id
    workflow.updated_at = _now()

    wcr.status = "applied"
    wcr.decided_by = actor.id
    wcr.decided_at = _now()
    wcr.applied_at = _now()

    report: dict[str, Any] = {
        "migrated_requests": outcome.migrated_total,
        "by_status": outcome.by_status,
        "digest_notifications": digests,
        "timings": {"migrate_ms": migrate_ms},
    }
    # переприсваивание, а не мутация на месте: JSONB не отслеживает изменения вложенных dict
    wcr.impact = {**(wcr.impact or {}), "migration_report": copy.deepcopy(report)}

    # одна запись аудита на пакет; поштучный след — строки deal_stage_history
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
            "migrated_requests": outcome.migrated_total,
            "by_status": outcome.by_status,
            "digest_notifications": digests,
        },
    )
    await session.flush()
    return report


def _store_timings(
    wcr: WorkflowChangeRequest, report: dict[str, Any], *, started: float, locked: float
) -> None:
    """Дописывает замеры в отчёт и в сохранённый `impact['migration_report']` (до COMMIT):
    `lock_wait_ms` — ожидание эксклюзивного лока, `apply_ms` — от захвата до COMMIT."""
    timings = report.setdefault("timings", {})
    timings["lock_wait_ms"] = _ms(locked - started)
    timings["apply_ms"] = _ms(time.perf_counter() - locked)
    wcr.impact = {**(wcr.impact or {}), "migration_report": copy.deepcopy(report)}
    flag_modified(wcr, "impact")


async def _after_apply_effects(
    workflow_id: uuid.UUID, change_id: uuid.UUID, *, migrated_requests: int = 0
) -> None:
    await invalidate_workflow_cache(workflow_id)
    await publish_event(
        SSE_WORKFLOW_CHANGED,
        {
            "workflow_id": str(workflow_id),
            "change_id": str(change_id),
            "migrated_requests": migrated_requests,
        },
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

    Классификация: пакет с опасной операцией — всегда pending (даже от автора с правом
    согласования, двухшаговое подтверждение); безопасный от автора с `workflow:approve`
    в процессе воронки — применяется сразу; без этого права — pending на согласование.
    Право `workflow:change` в процессе проверяет обработчик запроса.
    """
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise not_found("Воронка не найдена")
    live = await load_graph(session, workflow_id)
    try:
        verify_confirm_names(live, operations)  # рубеж 1 «защиты от дурака»
    except (OperationConflict, ConfirmNameMismatch) as exc:
        raise _wrap_graph_errors(exc) from exc
    impact, _plan = await compute_impact(session, live, operations, workflow_code=workflow.code)

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
    apply_now = not dangerous and author.access.has("workflow:approve", workflow.code)

    if apply_now:
        started = time.perf_counter()
        # ожидание лока ограничено APPLY_LOCK_TIMEOUT_MS; при 409 workflow_busy пакет
        # не создаётся вовсе (транзакция откатывается) — повтор запроса безопасен
        async with lock_timeout_as_busy():
            await acquire_exclusive_workflow_lock(session, workflow_id)
            locked = time.perf_counter()
            # схему никто не менял с момента чтения выше только под локом — перечитываем
            live = await load_graph(session, workflow_id)
            report = await _execute_plan(session, wcr, author, live, operations)
            _store_timings(wcr, report, started=started, locked=locked)
            await session.commit()
        await _after_apply_effects(
            workflow_id, wcr.id, migrated_requests=report["migrated_requests"]
        )
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
            f"«{workflow.name}» — требуется согласование"
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
    """Approve = атомарное применение одной транзакцией (workflow-engine.md §6.4):
    схема, перенос заявок, история, сводки в outbox и аудит — либо всё, либо ничего.

    - Лок воронки не получен за `APPLY_LOCK_TIMEOUT_MS` (хвост переходов) — 409
      `workflow_busy`, пакет остаётся `pending`: он не виноват, применить можно повторно.
    - Любая другая ошибка применения откатывает всё (заявки не тронуты, уведомлений нет),
      пакет помечается `failed` отдельной транзакцией — автор пересоздаёт
      (state-машина data-model.md §5.2).

    Замеры (`timings`: lock_wait_ms, apply_ms, migrate_ms; в ответе ещё lock_held_ms)
    попадают в отчёт, в `impact['migration_report']` и в лог `app.workflow`.
    """
    # id актора — до любых rollback: откат истекает все объекты сессии, а ленивая
    # подгрузка атрибута в async-коде невозможна (MissingGreenlet)
    approver_id = approver.id
    wcr = await session.get(WorkflowChangeRequest, change_id, with_for_update=True)
    if wcr is None:
        raise not_found("Пакет изменений не найден")
    if wcr.status != "pending":
        raise conflict("Пакет уже обработан")

    started = time.perf_counter()
    try:
        # эксклюзивный лок: дожидаемся хвоста переходов, замораживаем новые (§6.5)
        async with lock_timeout_as_busy(APPLY_BUSY_MESSAGE):
            await acquire_exclusive_workflow_lock(session, wcr.workflow_id)
    except ApiError:
        await session.rollback()
        raise
    locked = time.perf_counter()
    try:
        async with lock_timeout_as_busy(APPLY_BUSY_MESSAGE):
            live = await load_graph(session, wcr.workflow_id)
            operations = parse_operations(wcr.operations)
            report = await _execute_plan(session, wcr, approver, live, operations)
            _store_timings(wcr, report, started=started, locked=locked)
            await session.commit()
    except ApiError as exc:
        await session.rollback()
        if exc.code != "workflow_busy":
            await _mark_change_failed(session, change_id, approver_id, exc.message)
        raise
    except Exception:
        # сбой посреди применения (ошибка БД, нарушенный инвариант): транзакция откатана
        # целиком — схема, перенос, история и сводки; пакет помечается failed
        logger.exception("workflow.apply change=%s: ошибка применения, откат", change_id)
        await session.rollback()
        try:
            await _mark_change_failed(session, change_id, approver_id, INTERNAL_APPLY_ERROR)
        except Exception:  # noqa: BLE001 — исходная ошибка важнее, её и пробрасываем
            logger.exception("workflow.apply change=%s: не удалось пометить failed", change_id)
            await session.rollback()
        raise
    report["timings"]["lock_held_ms"] = _ms(time.perf_counter() - locked)
    logger.info(
        "workflow.apply change=%s migrated=%d digests=%d lock_wait_ms=%d lock_held_ms=%d",
        change_id,
        report["migrated_requests"],
        report["digest_notifications"],
        report["timings"]["lock_wait_ms"],
        report["timings"]["lock_held_ms"],
    )
    await _after_apply_effects(
        wcr.workflow_id, wcr.id, migrated_requests=report["migrated_requests"]
    )
    return wcr, report


async def _mark_change_failed(
    session: AsyncSession, change_id: uuid.UUID, approver_id: uuid.UUID, error: str
) -> None:
    wcr = await session.get(WorkflowChangeRequest, change_id, with_for_update=True)
    if wcr is None or wcr.status != "pending":
        return
    wcr.status = "failed"
    wcr.decided_by = approver_id
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


def _pick_stuck_rule(rules: list[NotificationRule], deal: Deal) -> NotificationRule | None:
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
        await session.execute(
            select(Deal, WorkflowStage, Workflow)
            .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
            .join(Workflow, Workflow.id == Deal.workflow_id)
            .where(
                Deal.status == "open",
                WorkflowStage.is_terminal.is_(False),
                func.now() - Deal.stage_entered_at
                > func.make_interval(
                    0,
                    0,
                    0,
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
            dedup_key=stuck_dedup_key(deal.id, deal.stage_id, "responsible", days, repeat_days),
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
                dedup_key=stuck_dedup_key(deal.id, deal.stage_id, "manager", days, mgr_repeat),
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
