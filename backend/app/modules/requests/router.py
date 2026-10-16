"""Заявки B2B/B2C: CRUD, канбан, переходы, история, комментарии, файлы (api-contract.md §4).

Ресурс API — /requests, таблица — deal (мост Р-2). Смена этапа — только через
POST /transitions (инвариант I-4, движок — app.modules.workflow.service).

Резолюции по иерархии data-model > api-contract (задокументированы и в итогах стадии):
- DELETE /requests/{id} — «мягкое удаление» реализовано как status='archived'
  (в DDL deal нет deleted_at); архивные заявки исключены из всех выдач;
- «взять свободную заявку на себя» (`requests:assign` без `requests:assign_any`):
  свободной считается заявка, чей текущий ответственный не ведёт портфель как КАМ —
  нет права `requests:kam` в процессе заявки (responsible NOT NULL в DDL, поэтому
  NULL-назначения не существует — лиды CMS назначаются head_kam до распределения).

Права и область (F2): эндпоинты закрыты `require_permission`; область роли (процессы
B2B/B2C, «только свои») фильтрует списки и доску, заявка вне области — 404 (Р-F2-10),
доска и создание в процессе вне области — 403 с понятным текстом.

Файлы заявки (`entity_type='deal'`, бакет crm-files): список — `requests:read`; загрузка —
`requests:files` в процессе заявки; удаление — `requests:files` и автор файла либо
`requests:write_any` в процессе. Скачивание — общий `GET /files/{id}/download` (вне области
заявки — 404, без `files:business` — 403). Загрузка КАМом — взаимодействие в отчёте
«Активность КАМов».

Файлы в статусах (ТЗ, ФТ3): белый список 10 форматов (`check_attachment_format`); каждый
файл привязан к строке истории (`file_object.stage_history_id`) — к переходу, на котором его
приложили (модалка перехода: файлы загружаются с `pending_transition=true`, затем
`POST /transitions` с `file_ids` привязывает их к строке перехода), либо ко входу в текущий
этап (вкладка «Файлы»). История отдаёт файлы под их переходом.

Передача портфеля (`POST /requests/transfer-portfolio`): открытые заявки КАМа в процессах,
где у пользователя `requests:assign_any`, и закреплённые вузы (`universities:assign`) —
другому сотруднику одной транзакцией; аудит `request.assigned` по каждой заявке и
`university.kam_changed` по каждому вузу. История, комментарии, файлы и авторство прошлых
действий не меняются.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.cache import get_cache
from app.core.config import get_settings
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
from app.core.rbac_catalog import WORKFLOW_TYPE_LABELS
from app.core.security import require_permission
from app.core.sse import publish_event
from app.models.crm import Contract, Counterparty, InteractionType, Product, Program, University
from app.models.deal import Deal, DealComment, DealStageHistory
from app.models.service import AuditLog, FileObject
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.crm.deps import check_version
from app.modules.files.service import soft_delete_file, store_upload
from app.modules.requests.schemas import (
    AssignBody,
    CommentCreate,
    PortfolioTransferBody,
    ReopenBody,
    RequestCreate,
    RequestFileOut,
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

_READ = Depends(require_permission("requests:read"))

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


async def _load_deal(session: AsyncSession, request_id: uuid.UUID, user: AppUser) -> Deal:
    """Заявка в области пользователя; вне области — 404, как несуществующая (Р-F2-10)."""
    deal = await session.get(Deal, request_id)
    if deal is None or deal.status == "archived":
        raise not_found("Заявка не найдена")
    scope = user.access.deal_scope("requests:read", user.id)
    if not scope.allows(deal.pipeline, deal.responsible_user_id):
        raise not_found("Заявка не найдена")
    return deal


async def _deal_ref_in_scope(
    session: AsyncSession, request_id: uuid.UUID, user: AppUser
) -> tuple[str, uuid.UUID]:
    """(процесс, ответственный) заявки в области пользователя, без загрузки сущности:
    движок workflow сам берёт строку под FOR UPDATE, сверяет версию и статус."""
    row = (
        await session.execute(
            select(Deal.pipeline, Deal.responsible_user_id).where(Deal.id == request_id)
        )
    ).one_or_none()
    scope = user.access.deal_scope("requests:read", user.id)
    if row is None or not scope.allows(row.pipeline, row.responsible_user_id):
        raise not_found("Заявка не найдена")
    return row.pipeline, row.responsible_user_id


def _workflow_label(workflow_type: str) -> str:
    return WORKFLOW_TYPE_LABELS.get(workflow_type, workflow_type)


async def _ensure_assignee_sees(
    session: AsyncSession, assignee_id: uuid.UUID, pipeline: str
) -> None:
    """Ответственный должен видеть заявки процесса — иначе заявка «пропадёт» у него
    из доски и карточка будет недоступна (роли только B2B/B2C)."""
    assignee = await session.get(AppUser, assignee_id)
    if assignee is not None and not assignee.access.has("requests:read", pipeline):
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "assignee_id",
                    "code": "workflow_not_allowed",
                    "message": (
                        f"Пользователю недоступен процесс {_workflow_label(pipeline)} — "
                        "назначьте сотрудника с доступом к нему"
                    ),
                }
            ],
        )


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
            details.append({"field": field_name, "code": "lookup_not_found", "message": message})
    if details:
        raise validation_error("Ошибка валидации данных", details)


def _stuck_condition(default_days: int = 14):
    return func.now() - Deal.stage_entered_at > func.make_interval(
        0,
        0,
        0,
        func.coalesce(
            WorkflowStage.stuck_threshold_days, Workflow.stuck_threshold_days, default_days
        ),
    )


@router.get("", summary="Список заявок", dependencies=[_READ])
async def list_requests(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
    params: Annotated[ListParams, Depends(list_params)],
    workflow_type: Annotated[Literal["b2b", "b2c"] | None, Query()] = None,
    status_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    # КАМ, вуз и продукт — повторяемые (`?assignee_id=a&assignee_id=b`, условие IN):
    # переход с дашборда на доску переносит мультифильтр целиком; один параметр — как раньше
    assignee_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    university_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    product_id: Annotated[list[uuid.UUID] | None, Query()] = None,
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
    stmt = user.access.deal_scope("requests:read", user.id).apply(stmt)
    if workflow_type:
        stmt = stmt.where(Deal.pipeline == workflow_type)
    if status_id:
        stmt = stmt.where(Deal.stage_id.in_(status_id))
    if assignee_id:
        stmt = stmt.where(Deal.responsible_user_id.in_(assignee_id))
    if university_id:
        stmt = stmt.where(Deal.university_id.in_(university_id))
    if product_id:
        stmt = stmt.where(Deal.product_id.in_(product_id))
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
        stmt = stmt.where(sa.or_(Deal.title.ilike(pattern), university_match, counterparty_match))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
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
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
    workflow_type: Annotated[
        Literal["b2b", "b2c"] | None,
        Query(description="Процесс; по умолчанию — первый доступный роли"),
    ] = None,
    assignee_id: Annotated[uuid.UUID | None, Query()] = None,
    cards_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    scope = user.access.deal_scope("requests:read", user.id)
    if workflow_type is None:
        workflow_type = scope.workflow_types[0] if scope.workflow_types else "b2b"
    if workflow_type not in scope.workflow_types:
        raise forbidden(f"Процесс {_workflow_label(workflow_type)} недоступен вашей роли")
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
        deals_stmt = scope.apply(deals_stmt)
        count = (
            await session.execute(select(func.count()).select_from(deals_stmt.subquery()))
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
            cards.append(serialize_board_card(deal, stage, workflow, assignee, client_display))
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
    user: Annotated[AppUser, Depends(require_permission("requests:write"))],
    request: Request,
) -> dict[str, Any]:
    if not user.access.has("requests:write", body.workflow_type):
        raise forbidden(
            f"Создание заявок в процессе {_workflow_label(body.workflow_type)} "
            "недоступно вашей роли"
        )
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
    if assignee_id != user.id:
        if not user.access.has("requests:assign_any", workflow.code):
            raise forbidden("KAM может назначить ответственным только себя")
        await _ensure_assignee_sees(session, assignee_id, workflow.code)

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
        session,
        deal,
        viewer=user,
        stage=initial_stage,
        workflow=workflow,
        available_transitions=await available_transitions(session, deal, user),
    )
    if warning is not None:
        payload["warning_duplicate"] = warning
    return payload


@router.post(
    "/transfer-portfolio",
    summary="Передать портфель КАМа (открытые заявки и вузы) другому сотруднику",
)
async def transfer_portfolio(
    body: PortfolioTransferBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:assign_any"))],
    request: Request,
) -> dict[str, Any]:
    """Уход или отпуск КАМа: открытые заявки и закреплённые вузы — другому одним действием.

    Заявки — только открытые, только в процессах, где у пользователя `requests:assign_any`
    (и в его области записей); заявку процесса, недоступного получателю, не передаём —
    она попадает в отчёт `skipped`. Вузы — с правом `universities:assign`. История,
    комментарии, файлы и авторство прошлых действий не меняются; `last_activity_at` не
    сдвигается — передача не контакт с вузом. `dry_run` — тот же отчёт без изменений.
    """
    from_user = await session.get(AppUser, body.from_user_id)
    if from_user is None:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "from_user_id",
                    "code": "lookup_not_found",
                    "message": "Пользователь не найден",
                }
            ],
        )
    to_user = await session.get(AppUser, body.to_user_id)
    if to_user is None or not to_user.is_active:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "to_user_id",
                    "code": "lookup_not_found",
                    "message": "Получатель не найден или деактивирован",
                }
            ],
        )
    if body.include_universities and not user.access.has("universities:assign"):
        raise forbidden(
            "Передача вузов недоступна вашей роли: нужно право "
            "«Назначение ответственного КАМа за вуз»"
        )

    scope = user.access.deal_scope("requests:assign_any", user.id)
    deals = (
        (
            await session.execute(
                scope.apply(
                    select(Deal)
                    .where(Deal.responsible_user_id == from_user.id, Deal.status == "open")
                    .order_by(Deal.created_at, Deal.id)
                )
            )
        )
        .scalars()
        .all()
    )
    transferred: list[Deal] = []
    skipped: list[dict[str, Any]] = []
    for deal in deals:
        if to_user.access.has("requests:read", deal.pipeline):
            transferred.append(deal)
        else:
            skipped.append(
                {
                    "id": str(deal.id),
                    "title": deal.title,
                    "workflow_type": deal.pipeline,
                    "reason": (f"Получателю недоступен процесс {_workflow_label(deal.pipeline)}"),
                }
            )
    universities: list[University] = []
    if body.include_universities:
        universities = list(
            (
                await session.execute(
                    select(University)
                    .where(University.kam_user_id == from_user.id)
                    .order_by(University.name)
                )
            )
            .scalars()
            .all()
        )

    by_workflow: dict[str, int] = {}
    for deal in transferred:
        by_workflow[deal.pipeline] = by_workflow.get(deal.pipeline, 0) + 1
    report: dict[str, Any] = {
        "dry_run": body.dry_run,
        "from_user": {"id": str(from_user.id), "full_name": from_user.full_name},
        "to_user": {"id": str(to_user.id), "full_name": to_user.full_name},
        "requests": {
            "transferred": len(transferred),
            "skipped": len(skipped),
            "by_workflow": by_workflow,
            "items": [
                {"id": str(d.id), "title": d.title, "workflow_type": d.pipeline}
                for d in transferred
            ],
            "skipped_items": skipped,
        },
        "universities": {
            "included": body.include_universities,
            "transferred": len(universities),
            "items": [
                {"id": str(u.id), "name": u.short_name or u.name, "is_active": u.is_active}
                for u in universities
            ],
        },
    }
    if body.dry_run or (not transferred and not universities):
        return report

    ip = client_ip(request)
    for deal in transferred:
        deal.responsible_user_id = to_user.id
        await write_audit(
            session,
            actor_user_id=user.id,
            action="request.assigned",
            entity_type="deal",
            entity_id=deal.id,
            before={"assignee_id": str(from_user.id)},
            after={"assignee_id": str(to_user.id), "reason": "portfolio_transfer"},
            ip=ip,
        )
    for university in universities:
        university.kam_user_id = to_user.id
        await write_audit(
            session,
            actor_user_id=user.id,
            action="university.kam_changed",
            entity_type="university",
            entity_id=university.id,
            before={"kam_user_id": str(from_user.id)},
            after={"kam_user_id": str(to_user.id), "reason": "portfolio_transfer"},
            ip=ip,
        )
    await write_audit(
        session,
        actor_user_id=user.id,
        action="user.portfolio_transferred",
        entity_type="app_user",
        entity_id=from_user.id,
        after={
            "to_user_id": str(to_user.id),
            "requests": len(transferred),
            "universities": len(universities),
            "skipped": len(skipped),
        },
        ip=ip,
    )
    if to_user.id != user.id:
        await emit_event_notifications(
            session,
            event_type=EVENT_REQUEST_ASSIGNED,
            entity_type="app_user",
            entity_id=to_user.id,
            subject="Вам передан портфель",
            body=(
                f"Вам передан портфель {from_user.full_name or from_user.username}: "
                f"заявок — {len(transferred)}, вузов — {len(universities)}"
            ),
            payload={
                "title": f"Портфель {from_user.full_name or from_user.username}",
                "url": f"/board?kam={to_user.id}",
            },
            direct_recipients=[DirectRecipient(user=to_user)],
        )
    await session.commit()
    cache = get_cache()
    for deal in transferred:
        await cache.delete(f"deal:{deal.id}")
    if universities:
        await cache.delete("dict:universities")
    return report


@router.get("/{request_id}", summary="Карточка заявки", dependencies=[_READ])
async def get_request(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
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
    user: Annotated[AppUser, Depends(require_permission("requests:write"))],
    request: Request,
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
    if not user.access.can_act_on_deal(
        "requests:write",
        pipeline=deal.pipeline,
        responsible_user_id=deal.responsible_user_id,
        user_id=user.id,
    ):
        raise forbidden("Изменять заявку может только её ответственный")
    check_version(deal, body.version)

    updates = body.model_dump(exclude_unset=True, exclude={"version"})
    await _validate_refs(session, updates)
    # клиентский инвариант pipeline (CHECK ck_deal_client): не даём занулить владельца
    if deal.pipeline == "b2b" and updates.get("university_id", deal.university_id) is None:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "university_id",
                    "code": "required",
                    "message": "B2B-заявка не может остаться без вуза",
                }
            ],
        )
    if deal.pipeline == "b2c" and updates.get("counterparty_id", deal.counterparty_id) is None:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "counterparty_id",
                    "code": "required",
                    "message": "B2C-заявка не может остаться без клиента",
                }
            ],
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
        session,
        deal,
        viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.post("/{request_id}/assign", summary="Назначить ответственного")
async def assign_request(
    request_id: uuid.UUID,
    body: AssignBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:assign"))],
    request: Request,
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
    if not user.access.has("requests:assign", deal.pipeline):
        raise forbidden(
            f"Назначение ответственных в процессе {_workflow_label(deal.pipeline)} "
            "недоступно вашей роли"
        )
    if body.version is not None:
        check_version(deal, body.version)
    new_assignee = await session.get(AppUser, body.assignee_id)
    if new_assignee is None or not new_assignee.is_active:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "assignee_id",
                    "code": "lookup_not_found",
                    "message": "Пользователь не найден или деактивирован",
                }
            ],
        )
    if not user.access.has("requests:assign_any", deal.pipeline):
        # без assign_any — только взять «свободную» заявку на себя (см. докстринг модуля)
        current = await session.get(AppUser, deal.responsible_user_id)
        if body.assignee_id != user.id:
            raise forbidden("KAM может назначить ответственным только себя")
        if (
            current is not None
            and current.id != user.id
            and current.access.has("requests:kam", deal.pipeline)
        ):
            raise forbidden("Заявка уже назначена другому KAM")
    await _ensure_assignee_sees(session, new_assignee.id, deal.pipeline)

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
        session,
        deal,
        viewer=user,
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
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
    items = await available_transitions(session, deal, user)
    return {"items": items}


@router.post("/{request_id}/transitions", summary="Выполнить переход по статусу")
async def perform_transition(
    request_id: uuid.UUID,
    body: TransitionBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:transition"))],
    request: Request,
) -> dict[str, Any]:
    # область и право в процессе заявки — до движка (вне области — 404, Р-F2-10)
    pipeline, responsible_id = await _deal_ref_in_scope(session, request_id, user)
    if not user.access.can_act_on_deal(
        "requests:transition",
        pipeline=pipeline,
        responsible_user_id=responsible_id,
        user_id=user.id,
    ):
        raise forbidden("Переход доступен только ответственному по заявке")
    # файлы перехода проверяются ДО движка: невалидный файл не должен оставить заявку
    # переведённой без своего вложения; привязка к строке истории — в транзакции перехода
    files = await _transition_files(session, request_id, pipeline, body.file_ids, user)
    deal = await workflow_service.transition_deal(
        session,
        deal_id=request_id,
        to_status_id=body.to_status_id,
        actor=user,
        comment=body.comment,
        version=body.version,
        ip=client_ip(request),
        files=files,
    )
    return await serialize_deal(
        session,
        deal,
        viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.get("/{request_id}/history", summary="История заявки", dependencies=[_READ])
async def request_history(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
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
            for row in (await session.execute(select(AppUser).where(AppUser.id.in_(actor_ids))))
            .scalars()
            .all()
        }
    # файлы в статусах: каждый файл — под строкой истории, к которой он приложен
    files_by_row: dict[str, list[dict[str, Any]]] = {}
    for file_item in await request_files_payload(session, await _deal_files(session, deal.id)):
        if file_item["stage_history_id"]:
            files_by_row.setdefault(file_item["stage_history_id"], []).append(file_item)
    items = [
        {**serialize_history_row(row, stages, users), "files": files_by_row.get(str(row.id), [])}
        for row in history_rows
    ]
    for row in audit_rows:
        item = serialize_audit_history_item(row, users)
        if item is not None:
            items.append({**item, "files": []})
    items.sort(key=lambda item: item["created_at"])
    return {"items": items, "total": len(items)}


# --- комментарии (встроенный мессенджер, §4.4) --------------------------------------------


@router.get("/{request_id}/comments", summary="Комментарии", dependencies=[_READ])
async def list_comments(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
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
            for row in (await session.execute(select(AppUser).where(AppUser.id.in_(author_ids))))
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
    user: Annotated[AppUser, Depends(require_permission("comments:write"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
    if not user.access.has("comments:write", deal.pipeline):
        raise forbidden(
            f"Комментарии в процессе {_workflow_label(deal.pipeline)} недоступны вашей роли"
        )
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
    summary="Удалить комментарий (автор или право удалять чужие)",
)
async def delete_comment(
    request_id: uuid.UUID,
    comment_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("comments:write"))],
) -> Response:
    deal = await _load_deal(session, request_id, user)
    comment = await session.get(DealComment, comment_id)
    if comment is None or comment.deal_id != deal.id or comment.deleted_at is not None:
        raise not_found("Комментарий не найден")
    if not user.access.has("comments:write", deal.pipeline):
        raise forbidden(
            f"Комментарии в процессе {_workflow_label(deal.pipeline)} недоступны вашей роли"
        )
    if comment.author_user_id != user.id and not user.access.has(
        "comments:delete_any", deal.pipeline
    ):
        raise forbidden("Удалить комментарий может только автор или администратор")
    comment.deleted_at = _now()
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    return Response(status_code=204)


# --- файлы заявки (MinIO, bucket crm-files; entity_type='deal') ----------------------------


def _ensure_files_permission(user: AppUser, deal: Deal) -> None:
    if not user.access.has("requests:files", deal.pipeline):
        raise forbidden(
            f"Файлы заявок процесса {_workflow_label(deal.pipeline)} недоступны вашей роли"
        )


async def _current_stage_entry_id(
    session: AsyncSession, deal_id: uuid.UUID, stage_id: uuid.UUID
) -> uuid.UUID | None:
    """Строка истории, которой заявка вошла в текущий этап (переход, создание, миграция
    схемы): файл из вкладки «Файлы» приложен «в этом статусе»."""
    return (
        await session.execute(
            select(DealStageHistory.id)
            .where(DealStageHistory.deal_id == deal_id, DealStageHistory.to_stage_id == stage_id)
            .order_by(DealStageHistory.created_at.desc(), DealStageHistory.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _transition_files(
    session: AsyncSession,
    deal_id: uuid.UUID,
    pipeline: str,
    file_ids: list[uuid.UUID],
    user: AppUser,
) -> list[FileObject]:
    """Файлы, которые пользователь прикладывает к переходу (`file_ids`).

    Это файлы, только что загруженные им в заявку «для перехода» (`POST /files` с
    `pending_transition=true` — без привязки к этапу): свои, не удалённые и ещё ни к чему
    не привязанные. Файл, уже приложенный к переходу или этапу, второй раз не приложить.
    Иначе — 400 до движка: переход без обещанного вложения хуже, чем повтор действия.
    """
    if not file_ids:
        return []
    if not user.access.has("requests:files", pipeline):
        raise forbidden(f"Файлы заявок процесса {_workflow_label(pipeline)} недоступны вашей роли")
    unique_ids = list(dict.fromkeys(file_ids))
    found = {
        row.id: row
        for row in (
            await session.execute(select(FileObject).where(FileObject.id.in_(unique_ids)))
        ).scalars()
    }
    details = []
    for file_id in unique_ids:
        file_object = found.get(file_id)
        if (
            file_object is None
            or file_object.entity_type != "deal"
            or file_object.entity_id != deal_id
            or file_object.deleted_at is not None
            or file_object.uploaded_by != user.id
            or file_object.stage_history_id is not None
        ):
            details.append(
                {
                    "field": "file_ids",
                    "code": "lookup_not_found",
                    "message": (
                        f"Файл {file_id} не найден среди ваших новых файлов заявки "
                        "или уже приложен к другому переходу"
                    ),
                }
            )
    if details:
        raise validation_error("Ошибка валидации данных", details)
    return [found[file_id] for file_id in unique_ids]


async def request_files_payload(
    session: AsyncSession,
    files: list[FileObject],
) -> list[dict[str, Any]]:
    """Файлы заявки для API: этап (снапшот имени из строки истории) и имя автора."""
    history_ids = {f.stage_history_id for f in files if f.stage_history_id}
    stage_names: dict[uuid.UUID, str] = {}
    if history_ids:
        stage_names = {
            row.id: row.to_stage_name
            for row in (
                await session.execute(
                    select(DealStageHistory.id, DealStageHistory.to_stage_name).where(
                        DealStageHistory.id.in_(history_ids)
                    )
                )
            ).all()
        }
    author_ids = {f.uploaded_by for f in files if f.uploaded_by}
    authors: dict[uuid.UUID, str | None] = {}
    if author_ids:
        authors = {
            row.id: row.full_name
            for row in (
                await session.execute(
                    select(AppUser.id, AppUser.full_name).where(AppUser.id.in_(author_ids))
                )
            ).all()
        }
    return [
        RequestFileOut(
            id=f.id,
            file_name=f.original_name,
            content_type=f.content_type,
            size_bytes=f.size_bytes,
            s3_key=f.s3_key,
            uploaded_by=f.uploaded_by,
            uploaded_by_name=authors.get(f.uploaded_by) if f.uploaded_by else None,
            created_at=f.created_at,
            stage_history_id=f.stage_history_id,
            stage_name=stage_names.get(f.stage_history_id) if f.stage_history_id else None,
        ).model_dump(mode="json")
        for f in files
    ]


async def _deal_files(session: AsyncSession, deal_id: uuid.UUID) -> list[FileObject]:
    return list(
        (
            await session.execute(
                select(FileObject)
                .where(
                    FileObject.entity_type == "deal",
                    FileObject.entity_id == deal_id,
                    FileObject.deleted_at.is_(None),
                )
                .order_by(FileObject.created_at, FileObject.id)
            )
        )
        .scalars()
        .all()
    )


@router.get(
    "/{request_id}/files",
    summary="Файлы заявки (с этапом, в котором файл приложен)",
    dependencies=[_READ],
)
async def list_request_files(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:read"))],
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
    items = await request_files_payload(session, await _deal_files(session, deal.id))
    return {"items": items, "total": len(items)}


@router.post(
    "/{request_id}/files",
    status_code=201,
    summary="Загрузить файл в заявку (до 50 МБ; PNG, JPEG, PDF, ZIP, GZIP, RAR, DOC(X), XLS(X))",
)
async def upload_request_file(
    request_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="Вложение заявки (до 50 МБ, 10 форматов ТЗ)")],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:files"))],
    request: Request,
    stage_history_id: Annotated[
        uuid.UUID | None,
        Form(
            description=(
                "Строка истории заявки (переход), к которой приложить файл; "
                "по умолчанию — вход в текущий этап"
            )
        ),
    ] = None,
    pending_transition: Annotated[
        bool,
        Form(
            description=(
                "Файл для перехода из модалки: без привязки, пока `POST /transitions` "
                "с `file_ids` не приложит его к строке истории перехода"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    deal = await _load_deal(session, request_id, user)
    _ensure_files_permission(user, deal)
    if pending_transition:
        if stage_history_id is not None:
            raise validation_error(
                "Ошибка валидации данных",
                [
                    {
                        "field": "stage_history_id",
                        "code": "invalid",
                        "message": "Файл для перехода привязывается к переходу при его выполнении",
                    }
                ],
            )
    elif stage_history_id is not None:
        history_deal = (
            await session.execute(
                select(DealStageHistory.deal_id).where(DealStageHistory.id == stage_history_id)
            )
        ).scalar_one_or_none()
        if history_deal != deal.id:
            raise validation_error(
                "Ошибка валидации данных",
                [
                    {
                        "field": "stage_history_id",
                        "code": "lookup_not_found",
                        "message": "Переход не найден в истории этой заявки",
                    }
                ],
            )
    else:
        stage_history_id = await _current_stage_entry_id(session, deal.id, deal.stage_id)
    file_object = await store_upload(
        session,
        upload=file,
        bucket=get_settings().s3_bucket_files,
        entity_type="deal",
        entity_id=deal.id,
        uploaded_by=user.id,
        check_format=True,
        stage_history_id=stage_history_id,
    )
    deal.last_activity_at = _now()
    await write_audit(
        session,
        actor_user_id=user.id,
        action="request.file_uploaded",
        entity_type="deal",
        entity_id=deal.id,
        after={
            "file_id": str(file_object.id),
            "file_name": file_object.original_name,
            "stage_history_id": str(stage_history_id) if stage_history_id else None,
        },
        ip=client_ip(request),
    )
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    (payload,) = await request_files_payload(session, [file_object])
    return payload


@router.delete(
    "/{request_id}/files/{file_id}",
    status_code=204,
    summary="Удалить файл заявки (автор или право править чужие заявки)",
)
async def delete_request_file(
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:files"))],
    request: Request,
) -> Response:
    deal = await _load_deal(session, request_id, user)
    _ensure_files_permission(user, deal)
    file_object = await session.get(FileObject, file_id)
    if (
        file_object is None
        or file_object.entity_type != "deal"
        or file_object.entity_id != deal.id
        or file_object.deleted_at is not None
    ):
        raise not_found("Файл не найден")
    if file_object.uploaded_by != user.id and not user.access.has(
        "requests:write_any", deal.pipeline
    ):
        raise forbidden("Удалить файл может только его автор или руководитель")
    soft_delete_file(file_object)  # объект в MinIO чистит POST /internal/jobs/purge-deleted
    await write_audit(
        session,
        actor_user_id=user.id,
        action="request.file_deleted",
        entity_type="deal",
        entity_id=deal.id,
        before={"file_id": str(file_object.id), "file_name": file_object.original_name},
        ip=client_ip(request),
    )
    await session.commit()
    await get_cache().delete(f"deal:{deal.id}")
    return Response(status_code=204)


@router.post("/{request_id}/reopen", summary="Переоткрыть из терминального статуса")
async def reopen_request(
    request_id: uuid.UUID,
    body: ReopenBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:reopen"))],
    request: Request,
) -> dict[str, Any]:
    pipeline, _responsible_id = await _deal_ref_in_scope(session, request_id, user)
    if not user.access.has("requests:reopen", pipeline):
        raise forbidden(
            f"Переоткрытие заявок процесса {_workflow_label(pipeline)} недоступно вашей роли"
        )
    deal = await workflow_service.reopen_deal(
        session,
        deal_id=request_id,
        to_status_id=body.to_status_id,
        actor=user,
        comment=body.comment,
        ip=client_ip(request),
    )
    return await serialize_deal(
        session,
        deal,
        viewer=user,
        available_transitions=await available_transitions(session, deal, user),
    )


@router.delete("/{request_id}", status_code=204, summary="Мягкое удаление заявки")
async def delete_request(
    request_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("requests:delete"))],
    request: Request,
) -> Response:
    deal = await _load_deal(session, request_id, user)
    if not user.access.has("requests:delete", deal.pipeline):
        raise forbidden(
            f"Удаление заявок процесса {_workflow_label(deal.pipeline)} недоступно вашей роли"
        )
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
