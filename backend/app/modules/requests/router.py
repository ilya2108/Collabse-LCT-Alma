"""Заявки B2B/B2C: CRUD, канбан, переходы, история, комментарии (api-contract.md §4).

Ресурс API — /requests, таблица — deal (мост Р-2). Смена этапа — только через
POST /transitions (инвариант I-4, движок — app.modules.workflow.service).

Резолюции по иерархии data-model > api-contract (задокументированы и в итогах стадии):
- DELETE /requests/{id} — «мягкое удаление» реализовано как status='archived'
  (в DDL deal нет deleted_at); архивные заявки исключены из всех выдач;
- kam «берёт свободную заявку на себя»: свободной считается заявка, чей текущий
  ответственный не имеет роли kam (responsible NOT NULL в DDL, поэтому NULL-назначения
  не существует — лиды CMS назначаются head_kam до распределения).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.cache import get_cache
from app.core.db import get_session
from app.core.errors import forbidden, not_found, validation_error
from app.core.events import (
    EVENT_REQUEST_ASSIGNED,
    EVENT_REQUEST_COMMENT_ADDED,
    EVENT_REQUEST_CREATED,
    SSE_REQUEST_COMMENT_ADDED,
)
from app.core.outbox import DirectRecipient, emit_event_notifications
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import check_roles, get_current_user, require_roles
from app.core.sse import publish_event
from app.models.crm import Contract, Counterparty, InteractionType, Product, Program, University
from app.models.deal import Deal, DealComment, DealStageHistory
from app.models.service import AuditLog
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.crm.deps import check_version
from app.modules.requests.schemas import (
    AssignBody,
    CommentCreate,
    ReopenBody,
    RequestCreate,
    RequestUpdate,
    TransitionBody,
)
from app.modules.requests.serializers import (
    serialize_audit_history_item,
    serialize_board_card,
    serialize_comment,
    serialize_deal,
    serialize_history_row,
)
from app.modules.requests.service import available_transitions
from app.modules.workflow import service as workflow_service

router = APIRouter(prefix="/requests", tags=["requests"])

_READ = Depends(get_current_user)

_SORT_FIELDS = {
    "created_at": Deal.created_at,
    "updated_at": Deal.updated_at,
    "title": Deal.title,
    "amount": Deal.amount,
    "status_updated_at": Deal.stage_entered_at,
    "last_activity_at": Deal.last_activity_at,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_deal(session: AsyncSession, request_id: uuid.UUID) -> Deal:
    deal = await session.get(Deal, request_id)
    if deal is None or deal.status == "archived":
        raise not_found("Заявка не найдена")
    return deal


async def _validate_refs(session: AsyncSession, fields: dict[str, Any]) -> None:
    """FK-ссылки тела проверяются заранее — 400 с полем вместо 500 от IntegrityError."""
    lookups: list[tuple[str, type, str]] = [
        ("university_id", University, "Вуз не найден"),
        ("counterparty_id", Counterparty, "Контрагент не найден"),
        ("interaction_type_id", InteractionType, "Тип взаимодействия не найден"),
        ("contract_id", Contract, "Договор не найден"),
        ("product_id", Product, "Продукт не найден"),
        ("program_id", Program, "Программа не найдена"),
        ("assignee_id", AppUser, "Пользователь не найден"),
    ]
    details = []
    for field_name, model, message in lookups:
        value = fields.get(field_name)
        if value is not None and await session.get(model, value) is None:
            details.append(
                {"field": field_name, "code": "lookup_not_found", "message": message}
            )
    if details:
        raise validation_error("Ошибка валидации данных", details)


def _stuck_condition(default_days: int = 14):
    return func.now() - Deal.stage_entered_at > func.make_interval(
        0, 0, 0,
        func.coalesce(
            WorkflowStage.stuck_threshold_days, Workflow.stuck_threshold_days, default_days
        ),
    )


@router.get("", summary="Список заявок", dependencies=[_READ])
async def list_requests(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
    params: Annotated[ListParams, Depends(list_params)],
    workflow_type: Annotated[Literal["b2b", "b2c"] | None, Query()] = None,
    status_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    assignee_id: Annotated[uuid.UUID | None, Query()] = None,
    university_id: Annotated[uuid.UUID | None, Query()] = None,
    product_id: Annotated[uuid.UUID | None, Query()] = None,
    program_id: Annotated[uuid.UUID | None, Query()] = None,
    stuck: Annotated[bool | None, Query()] = None,
    created_from: Annotated[date | None, Query()] = None,
    created_to: Annotated[date | None, Query()] = None,
    source: Annotated[Literal["manual", "import", "cms", "lms"] | None, Query()] = None,
    search: Annotated[str | None, Query(description="Название / клиент")] = None,
) -> dict[str, Any]:
    stmt = (
        select(Deal, WorkflowStage, Workflow)
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(Deal.status != "archived")
    )
    if workflow_type:
        stmt = stmt.where(Deal.pipeline == workflow_type)
    if status_id:
        stmt = stmt.where(Deal.stage_id.in_(status_id))
    if assignee_id:
        stmt = stmt.where(Deal.responsible_user_id == assignee_id)
    if university_id:
        stmt = stmt.where(Deal.university_id == university_id)
    if product_id:
        stmt = stmt.where(Deal.product_id == product_id)
    if program_id:
        stmt = stmt.where(Deal.program_id == program_id)
    if source:
        stmt = stmt.where(Deal.source == source)
    if created_from:
        stmt = stmt.where(Deal.created_at >= created_from)
    if created_to:
        stmt = stmt.where(Deal.created_at < created_to + timedelta(days=1))
    if stuck is not None:
        stuck_clause = sa.and_(
            Deal.status == "open",
            WorkflowStage.is_terminal.is_(False),
            _stuck_condition(),
        )
        stmt = stmt.where(stuck_clause if stuck else ~stuck_clause)
    if search:
        pattern = f"%{search.strip()}%"
        university_match = (
            select(University.id)
            .where(University.id == Deal.university_id, University.name.ilike(pattern))
            .exists()
        )
        counterparty_match = (
            select(Counterparty.id)
            .where(
                Counterparty.id == Deal.counterparty_id,
                Counterparty.display_name.ilike(pattern),
            )
            .exists()
        )
        stmt = stmt.where(
            sa.or_(Deal.title.ilike(pattern), university_match, counterparty_match)
        )

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="-created_at")
    rows = (await session.execute(stmt.limit(params.limit).offset(params.offset))).all()
    items = [
        await serialize_deal(session, deal, viewer=user, stage=stage, workflow=workflow)
        for deal, stage, workflow in rows
    ]
    return list_envelope(items, total, params)


@router.get("/board", summary="Канбан-агрегат", dependencies=[_READ])
async def board(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
    workflow_type: Annotated[Literal["b2b", "b2c"], Query()] = "b2b",
    assignee_id: Annotated[uuid.UUID | None, Query()] = None,
    cards_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    workflow = (
        await session.execute(select(Workflow).where(Workflow.code == workflow_type))
    ).scalar_one_or_none()
    if workflow is None:
        raise not_found("Воронка не найдена")
    stages = (
        (
            await session.execute(
                select(WorkflowStage)
                .where(
                    WorkflowStage.workflow_id == workflow.id,
                    WorkflowStage.deleted_at.is_(None),
                )
                .order_by(WorkflowStage.sort_order)
            )
        )
        .scalars()
        .all()
    )
    columns: list[dict[str, Any]] = []
    for stage in stages:
        deals_stmt = select(Deal).where(
            Deal.workflow_id == workflow.id,
            Deal.stage_id == stage.id,
            Deal.status != "archived",
        )
        # терминальные колонки показывают закрытые заявки, обычные — только открытые
        if not stage.is_terminal:
            deals_stmt = deals_stmt.where(Deal.status == "open")
        if assignee_id:
            deals_stmt = deals_stmt.where(Deal.responsible_user_id == assignee_id)
        count = (
            await session.execute(
                select(func.count()).select_from(deals_stmt.subquery())
            )
        ).scalar_one()
        deals = (
            (
                await session.execute(
                    deals_stmt.order_by(Deal.last_activity_at.desc()).limit(cards_limit)
                )
            )
            .scalars()
            .all()
        )
        cards = []
        for deal in deals:
            assignee = await session.get(AppUser, deal.responsible_user_id)
            client_display: str | None = None
            if deal.university_id:
                university = await session.get(University, deal.university_id)
                if university:
                    client_display = university.short_name or university.name
            elif deal.counterparty_id:
                counterparty = await session.get(Counterparty, deal.counterparty_id)
                if counterparty:
                    client_display = counterparty.display_name
            cards.append(
                serialize_board_card(deal, stage, workflow, assignee, client_display)
            )
        columns.append(
            {
                "status": {
                    "id": str(stage.id),
                    "code": stage.code,
                    "name": stage.name,
                    "color": stage.color,
                    "position": stage.sort_order,
                    "is_terminal": stage.is_terminal,
                },
                "count": count,
                "cards": cards,
            }
        )
    return {
        "workflow_id": str(workflow.id),
        "workflow_type": workflow.code,
        "columns": columns,
    }


@router.post("", status_code=201, summary="Создать заявку")
async def create_request(
    body: RequestCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> dict[str, Any]:
    workflow = (
        await session.execute(select(Workflow).where(Workflow.code == body.workflow_type))
    ).scalar_one_or_none()
    if workflow is None:
        raise not_found("Воронка не найдена")
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
        raise not_found("У воронки нет начального этапа")

    fields = body.model_dump(exclude={"workflow_type"})
    await _validate_refs(session, fields)
    assignee_id = fields.pop("assignee_id") or user.id
    if not check_roles(user.roles, ("admin", "head_kam")) and assignee_id != user.id:
        raise forbidden("KAM может назначить ответственным только себя")

    # workflow-engine.md §4.1 / checklist.md: ≥1 заявки на вуз — норма,
    # при создании 3-й активной — предупреждение, не запрет (как warning_duplicate договоров)
    warning: str | None = None
    if body.university_id is not None:
        open_count = (
            await session.execute(
                select(func.count()).where(
                    Deal.university_id == body.university_id,
                    Deal.status == "open",
                )
            )
        ).scalar_one()
        if open_count >= 2:
            noun = "взаимодействия" if open_count in (2, 3, 4) else "взаимодействий"
            warning = f"У вуза уже {open_count} активных {noun} — проверьте, не дубль ли это"

    deal = Deal(
        pipeline=workflow.code,
        workflow_id=workflow.id,
        stage_id=initial_stage.id,
        responsible_user_id=assignee_id,
        created_by=user.id,
        **fields,
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
            actor_user_id=user.id,
            kind="manual",
            reason="created",
        )
    )
    await emit_event_notifications(
        session,
        event_type=EVENT_REQUEST_CREATED,
        entity_type="deal",
        entity_id=deal.id,
        subject="Новая заявка",
        body=f"Создана заявка «{deal.title}» (этап «{initial_stage.name}»)",
        payload={
            "request_id": str(deal.id),
            "title": deal.title,
            "status": initial_stage.name,
            "url": f"/requests/{deal.id}",
        },
        deal=deal,
        exclude_user_ids=(user.id,),
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        action="request.created",
        entity_type="deal",
        entity_id=deal.id,
        after={"title": deal.title, "workflow_type": deal.pipeline},
        ip=client_ip(request),
    )
    await session.commit()
    payload = await serialize_deal(
        session, deal, viewer=user, stage=initial_stage, workflow=workflow,
        available_transitions=await available_transitions(session, deal, user),
    )
    if warning is not None:
        payload["warning_duplicate"] = warning
    return payload


@router.get("/{request_id}", summary="Карточка заявки", dependencies=[_READ])
async def get_request(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    comments = (
        (
            await session.execute(
                select(DealComment)
                .where(DealComment.deal_id == deal.id)
                .order_by(DealComment.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    last_comments = []
    for comment in comments:
        author = await session.get(AppUser, comment.author_user_id)
        last_comments.append(serialize_comment(comment, author))
    return await serialize_deal(
        session,
        deal,
        viewer=user,
        available_transitions=await available_transitions(session, deal, user),
        last_comments=last_comments,
    )


@router.patch("/{request_id}", summary="Изменить поля заявки (не статус)")
async def update_request(
    request_id: uuid.UUID,
    body: RequestUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    if not workflow_service.user_can_write_deal(user.roles, user.id, deal.responsible_user_id):
        raise forbidden("Изменять заявку может только её ответственный")
    check_version(deal, body.version)

    updates = body.model_dump(exclude_unset=True, exclude={"version"})
    await _validate_refs(session, updates)
    # клиентский инвариант pipeline (CHECK ck_deal_client): не даём занулить владельца
    if deal.pipeline == "b2b" and updates.get("university_id", deal.university_id) is None:
        raise validation_error(
            "Ошибка валидации данных",
            [{"field": "university_id", "code": "required",
              "message": "B2B-заявка не может остаться без вуза"}],
        )
    if deal.pipeline == "b2c" and updates.get("counterparty_id", deal.counterparty_id) is None:
        raise validation_error(
            "Ошибка валидации данных",
            [{"field": "counterparty_id", "code": "required",
              "message": "B2C-заявка не может остаться без клиента"}],
        )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field_name, new_value in updates.items():
        old_value = getattr(deal, field_name)
        if old_value != new_value:
            before[field_name] = str(old_value) if old_value is not None else None
            after[field_name] = str(new_value) if new_value is not None else None
            setattr(deal, field_name, new_value)
    if after:
        deal.last_activity_at = _now()
        await write_audit(
            session,
            actor_user_id=user.id,
            action="request.field_changed",
            entity_type="deal",
            entity_id=deal.id,
            before=before,
            after=after,
            ip=client_ip(request),
        )
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    return await serialize_deal(
        session, deal, viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.post("/{request_id}/assign", summary="Назначить ответственного")
async def assign_request(
    request_id: uuid.UUID,
    body: AssignBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    if body.version is not None:
        check_version(deal, body.version)
    new_assignee = await session.get(AppUser, body.assignee_id)
    if new_assignee is None or not new_assignee.is_active:
        raise validation_error(
            "Ошибка валидации данных",
            [{"field": "assignee_id", "code": "lookup_not_found",
              "message": "Пользователь не найден или деактивирован"}],
        )
    if not check_roles(user.roles, ("admin", "head_kam")):
        # kam: только взять «свободную» заявку на себя (см. резолюцию в докстринге модуля)
        current = await session.get(AppUser, deal.responsible_user_id)
        if body.assignee_id != user.id:
            raise forbidden("KAM может назначить ответственным только себя")
        if current is not None and "kam" in (current.roles or []) and current.id != user.id:
            raise forbidden("Заявка уже назначена другому KAM")

    previous_id = deal.responsible_user_id
    if previous_id != new_assignee.id:
        deal.responsible_user_id = new_assignee.id
        deal.last_activity_at = _now()
        await write_audit(
            session,
            actor_user_id=user.id,
            action="request.assigned",
            entity_type="deal",
            entity_id=deal.id,
            before={"assignee_id": str(previous_id)},
            after={"assignee_id": str(new_assignee.id)},
            ip=client_ip(request),
        )
        if new_assignee.id != user.id:
            await emit_event_notifications(
                session,
                event_type=EVENT_REQUEST_ASSIGNED,
                entity_type="deal",
                entity_id=deal.id,
                subject="Вам назначена заявка",
                body=f"Вы назначены ответственным по заявке «{deal.title}»",
                payload={
                    "request_id": str(deal.id),
                    "title": deal.title,
                    "url": f"/requests/{deal.id}",
                },
                direct_recipients=[DirectRecipient(user=new_assignee)],
            )
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    return await serialize_deal(
        session, deal, viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.get(
    "/{request_id}/transitions",
    summary="Доступные переходы для текущего пользователя",
    dependencies=[_READ],
)
async def list_available_transitions(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    items = await available_transitions(session, deal, user)
    return {"items": items}


@router.post("/{request_id}/transitions", summary="Выполнить переход по статусу")
async def perform_transition(
    request_id: uuid.UUID,
    body: TransitionBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> dict[str, Any]:
    deal = await workflow_service.transition_deal(
        session,
        deal_id=request_id,
        to_status_id=body.to_status_id,
        actor=user,
        comment=body.comment,
        version=body.version,
        ip=client_ip(request),
    )
    return await serialize_deal(
        session, deal, viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.get("/{request_id}/history", summary="История заявки", dependencies=[_READ])
async def request_history(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    history_rows = (
        (
            await session.execute(
                select(DealStageHistory)
                .where(DealStageHistory.deal_id == deal.id)
                .order_by(DealStageHistory.created_at)
            )
        )
        .scalars()
        .all()
    )
    audit_rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "deal",
                    AuditLog.entity_id == deal.id,
                    AuditLog.action.in_(("request.assigned", "request.field_changed")),
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    # этапы (включая soft-deleted — история читаема вечно) и актёры — одним махом
    stages = {
        row.id: row
        for row in (
            await session.execute(
                select(WorkflowStage).where(WorkflowStage.workflow_id == deal.workflow_id)
            )
        )
        .scalars()
        .all()
    }
    actor_ids = {r.actor_user_id for r in history_rows if r.actor_user_id} | {
        r.actor_user_id for r in audit_rows if r.actor_user_id
    }
    users = {}
    if actor_ids:
        users = {
            row.id: row
            for row in (
                await session.execute(select(AppUser).where(AppUser.id.in_(actor_ids))))
            .scalars()
            .all()
        }
    items = [serialize_history_row(row, stages, users) for row in history_rows]
    for row in audit_rows:
        item = serialize_audit_history_item(row, users)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: item["created_at"])
    return {"items": items, "total": len(items)}


# --- комментарии (встроенный мессенджер, §4.4) --------------------------------------------


@router.get("/{request_id}/comments", summary="Комментарии", dependencies=[_READ])
async def list_comments(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    comments = (
        (
            await session.execute(
                select(DealComment)
                .where(DealComment.deal_id == deal.id)
                .order_by(DealComment.created_at)
            )
        )
        .scalars()
        .all()
    )
    author_ids = {c.author_user_id for c in comments}
    authors = {}
    if author_ids:
        authors = {
            row.id: row
            for row in (
                await session.execute(select(AppUser).where(AppUser.id.in_(author_ids))))
            .scalars()
            .all()
        }
    items = [serialize_comment(c, authors.get(c.author_user_id)) for c in comments]
    return {"items": items, "total": len(items)}


@router.post("/{request_id}/comments", status_code=201, summary="Добавить комментарий")
async def add_comment(
    request_id: uuid.UUID,
    body: CommentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id)
    comment = DealComment(deal_id=deal.id, author_user_id=user.id, body=body.text.strip())
    session.add(comment)
    deal.last_activity_at = _now()
    await session.flush()
    await emit_event_notifications(
        session,
        event_type=EVENT_REQUEST_COMMENT_ADDED,
        entity_type="deal",
        entity_id=deal.id,
        subject="Новый комментарий",
        body=(
            f"{user.full_name or user.username} прокомментировал(а) заявку "
            f"«{deal.title}»: {body.text.strip()[:200]}"
        ),
        payload={
            "request_id": str(deal.id),
            "title": deal.title,
            "url": f"/requests/{deal.id}",
        },
        deal=deal,
        exclude_user_ids=(user.id,),
    )
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    await publish_event(SSE_REQUEST_COMMENT_ADDED, {"request_id": str(deal.id)})
    return serialize_comment(comment, user)


@router.delete(
    "/{request_id}/comments/{comment_id}",
    status_code=204,
    summary="Удалить комментарий (автор или admin)",
)
async def delete_comment(
    request_id: uuid.UUID,
    comment_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> Response:
    deal = await _load_deal(session, request_id)
    comment = await session.get(DealComment, comment_id)
    if comment is None or comment.deal_id != deal.id or comment.deleted_at is not None:
        raise not_found("Комментарий не найден")
    if comment.author_user_id != user.id and "admin" not in user.roles:
        raise forbidden("Удалить комментарий может только автор или администратор")
    comment.deleted_at = _now()
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    return Response(status_code=204)


@router.post("/{request_id}/reopen", summary="Переоткрыть из терминального статуса")
async def reopen_request(
    request_id: uuid.UUID,
    body: ReopenBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
    request: Request,
) -> dict[str, Any]:
    deal = await workflow_service.reopen_deal(
        session,
        deal_id=request_id,
        to_status_id=body.to_status_id,
        actor=user,
        comment=body.comment,
        ip=client_ip(request),
    )
    return await serialize_deal(
        session, deal, viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.delete("/{request_id}", status_code=204, summary="Мягкое удаление заявки")
async def delete_request(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
    request: Request,
) -> Response:
    deal = await _load_deal(session, request_id)
    before_status = deal.status
    deal.status = "archived"
    await write_audit(
        session,
        actor_user_id=user.id,
        action="request.deleted",
        entity_type="deal",
        entity_id=deal.id,
        before={"status": before_status},
        after={"status": "archived"},
        ip=client_ip(request),
    )
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    return Response(status_code=204)
