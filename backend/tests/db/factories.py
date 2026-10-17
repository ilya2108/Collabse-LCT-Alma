"""Фабрики данных для интеграционных тестов (tests/db) — общие для всех доработок.

Все функции keyword-only, пишут через переданную сессию и делают flush (не commit):
в `db_session` данные откатятся вместе с тестом, в `db_committed_sessionmaker` коммитит
сам тест. Значения по умолчанию соблюдают CHECK схемы (ck_deal_client,
ck_counterparty_kind_fields); время — tz-aware UTC. Фабрики конкретной доработки
(роли F2, пакеты схемы F3) живут в её собственных модулях рядом.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Activity,
    ActivitySchedule,
    AppSetting,
    AppUser,
    Counterparty,
    Deal,
    DealComment,
    DealStageHistory,
    FileObject,
    NotificationRule,
    University,
    Workflow,
    WorkflowStage,
)
from app.modules.notifications.prefs import SETTINGS_KEY_PREFIX

_seq = itertools.count(1)

# шаг истории: (stage_code, at[, actor[, kind[, is_return]]])
PathStep = tuple[Any, ...]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def days_ago(days: float, *, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=days)


def _id(value: Any) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return value.id


async def make_user(
    session: AsyncSession,
    *,
    roles: Iterable[str] = ("kam",),
    username: str | None = None,
    full_name: str | None = None,
    email: str | None = None,
    manager: AppUser | uuid.UUID | None = None,
    tg_chat_id: int | None = None,
    tg_username: str | None = None,
    is_active: bool = True,
    keycloak_sub: uuid.UUID | None = None,
) -> AppUser:
    n = next(_seq)
    username = username or f"user{n}@test"
    user = AppUser(
        keycloak_sub=keycloak_sub or uuid.uuid4(),
        username=username,
        email=email if email is not None else username,
        full_name=full_name or f"Тестов{n} Тест Тестович",
        roles=list(roles),
        manager_id=_id(manager),
        tg_chat_id=tg_chat_id,
        tg_username=tg_username,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


async def make_university(
    session: AsyncSession,
    *,
    name: str | None = None,
    short_name: str | None = None,
    kam: AppUser | uuid.UUID | None = None,
    region: str | None = "Москва",
    city: str | None = None,
    is_active: bool = True,
) -> University:
    n = next(_seq)
    university = University(
        name=name or f"Тестовый университет №{n}",
        short_name=short_name,
        region=region,
        city=city,
        kam_user_id=_id(kam),
        is_active=is_active,
    )
    session.add(university)
    await session.flush()
    return university


async def make_counterparty(
    session: AsyncSession,
    *,
    kind: str = "person",
    display_name: str | None = None,
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    org_name: str | None = None,
) -> Counterparty:
    """B2C-клиент. ПДн шифруются типом EncryptedStr; HMAC-колонки не заполняются."""
    n = next(_seq)
    if kind == "person":
        full_name = full_name or f"Клиентов{n} Иван Петрович"
        counterparty = Counterparty(
            kind="person",
            display_name=display_name or f"Клиентов{n} И. П.",
            full_name=full_name,
            email=email,
            phone=phone,
        )
    else:
        org_name = org_name or f"ООО «Тест-{n}»"
        counterparty = Counterparty(
            kind="legal_entity", display_name=display_name or org_name, org_name=org_name
        )
    session.add(counterparty)
    await session.flush()
    return counterparty


def initial_stage_code(stages: dict[str, WorkflowStage]) -> str:
    initial = [s for s in stages.values() if s.is_initial]
    chosen = initial[0] if initial else min(stages.values(), key=lambda s: s.sort_order)
    return chosen.code


async def make_deal(
    session: AsyncSession,
    *,
    workflow: Workflow,
    stages: dict[str, WorkflowStage],
    responsible: AppUser,
    university: University | uuid.UUID | None = None,
    counterparty: Counterparty | uuid.UUID | None = None,
    title: str | None = None,
    created_at: datetime | None = None,
    path: Sequence[PathStep] | None = None,
    stage_code: str | None = None,
    status: str | None = None,
    amount: Decimal | int | str | None = None,
    product_id: uuid.UUID | None = None,
    program_id: uuid.UUID | None = None,
    source: str = "manual",
) -> Deal:
    """Заявка с историей переходов.

    `path` — шаги `(stage_code, at[, actor[, kind[, is_return]]])`: первый пишется как
    создание (`from_stage_id = NULL`), actor по умолчанию — ответственный, kind — 'manual'.
    Без `path` — один шаг на `stage_code` (по умолчанию начальный этап) в `created_at`.
    `stage_id`, `stage_entered_at`, `last_activity_at` берутся из последнего шага; `status` —
    исход терминального этапа ('won'/'lost'), иначе 'open' (явный `status` важнее, например
    'archived'). Без вуза/клиента для своей воронки создаёт их сам (ck_deal_client).
    """
    n = next(_seq)
    created_at = created_at or utcnow()
    pipeline = workflow.code
    if pipeline == "b2b" and university is None:
        university = await make_university(session, kam=responsible)
    if pipeline == "b2c" and counterparty is None:
        counterparty = await make_counterparty(session)
    steps = list(path or [(stage_code or initial_stage_code(stages), created_at)])
    normalized: list[tuple[WorkflowStage, datetime, uuid.UUID | None, str, bool]] = []
    for step in steps:
        code, at, *rest = step
        actor = rest[0] if len(rest) > 0 else responsible
        kind = rest[1] if len(rest) > 1 else "manual"
        is_return = rest[2] if len(rest) > 2 else False
        normalized.append((stages[code], at, _id(actor), kind, is_return))
    last_stage, last_at = normalized[-1][0], normalized[-1][1]
    deal = Deal(
        pipeline=pipeline,
        workflow_id=workflow.id,
        stage_id=last_stage.id,
        title=title or f"Тестовая заявка №{n}",
        university_id=_id(university) if pipeline == "b2b" else None,
        counterparty_id=_id(counterparty) if pipeline == "b2c" else None,
        responsible_user_id=responsible.id,
        amount=Decimal(str(amount)) if amount is not None else None,
        status=status
        or (last_stage.terminal_outcome if last_stage.is_terminal else None)
        or "open",
        stage_entered_at=last_at,
        last_activity_at=last_at,
        source=source,
        created_by=responsible.id,
        product_id=product_id,
        program_id=program_id,
        created_at=created_at,
        updated_at=last_at,
    )
    session.add(deal)
    await session.flush()
    previous: WorkflowStage | None = None
    for stage, at, actor_id, kind, is_return in normalized:
        session.add(
            DealStageHistory(
                deal_id=deal.id,
                from_stage_id=previous.id if previous else None,
                to_stage_id=stage.id,
                from_stage_name=previous.name if previous else None,
                to_stage_name=stage.name,
                actor_user_id=actor_id,
                kind=kind,
                is_return=is_return,
                created_at=at,
            )
        )
        previous = stage
    await session.flush()
    return deal


async def make_comment(
    session: AsyncSession,
    *,
    deal: Deal,
    author: AppUser,
    at: datetime | None = None,
    body: str | None = None,
    deleted: bool = False,
) -> DealComment:
    at = at or utcnow()
    comment = DealComment(
        deal_id=deal.id,
        author_user_id=author.id,
        body=body or f"Комментарий №{next(_seq)}",
        created_at=at,
        deleted_at=at if deleted else None,
    )
    session.add(comment)
    await session.flush()
    return comment


async def make_file(
    session: AsyncSession,
    *,
    entity_id: uuid.UUID,
    uploaded_by: AppUser | uuid.UUID | None,
    entity_type: str = "deal",
    at: datetime | None = None,
    original_name: str | None = None,
    size_bytes: int = 4,
    deleted: bool = False,
) -> FileObject:
    """Только строка file_object (s3_key фиктивный); байты — фикстура memory_storage."""
    at = at or utcnow()
    file_id = uuid.uuid4()
    file_object = FileObject(
        id=file_id,
        s3_bucket="crm-files",
        s3_key=f"test/{entity_type}/{entity_id}/{file_id}",
        original_name=original_name or f"файл-{next(_seq)}.pdf",
        content_type="application/pdf",
        size_bytes=size_bytes,
        entity_type=entity_type,
        entity_id=entity_id,
        uploaded_by=_id(uploaded_by),
        created_at=at,
        deleted_at=at if deleted else None,
    )
    session.add(file_object)
    await session.flush()
    return file_object


async def make_activity(
    session: AsyncSession,
    *,
    university: University | uuid.UUID | None = None,
    responsible: AppUser | uuid.UUID | None = None,
    slots: Iterable[datetime] = (),
    name: str | None = None,
    activity_type: str = "event",
) -> Activity:
    activity = Activity(
        name=name or f"Мероприятие №{next(_seq)}",
        activity_type=activity_type,
        university_id=_id(university),
        responsible_user_id=_id(responsible),
    )
    session.add(activity)
    await session.flush()
    for starts_at in slots:
        session.add(ActivitySchedule(activity_id=activity.id, starts_at=starts_at))
    await session.flush()
    return activity


async def make_rule(
    session: AsyncSession,
    *,
    event_type: str,
    recipient_type: str,
    channel: str = "telegram",
    recipient_value: str | None = None,
    workflow: Workflow | uuid.UUID | None = None,
    stage: WorkflowStage | uuid.UUID | None = None,
    is_active: bool = True,
    created_by: AppUser | uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> NotificationRule:
    rule = NotificationRule(
        event_type=event_type,
        recipient_type=recipient_type,
        channel=channel,
        recipient_value=recipient_value,
        workflow_id=_id(workflow),
        stage_id=_id(stage),
        is_active=is_active,
        created_by=_id(created_by),
        params={},
    )
    if created_at is not None:
        rule.created_at = created_at
    session.add(rule)
    await session.flush()
    return rule


async def set_notify_prefs(
    session: AsyncSession, *, user: AppUser | uuid.UUID, doc: dict[str, Any]
) -> AppSetting:
    """Личные настройки уведомлений (`app_setting['notify:settings:{user_id}']`)."""
    key = f"{SETTINGS_KEY_PREFIX}{_id(user)}"
    setting = await session.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value=doc)
        session.add(setting)
    else:
        setting.value = doc
    await session.flush()
    return setting
