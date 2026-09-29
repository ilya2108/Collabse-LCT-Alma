"""Нотификации и Telegram-бот (api-contract.md §9).

- настройки каналов/событий, тест-отправка, in-app лента, маркеры прочтения;
- привязка Telegram: код 6 цифр TTL 10 мин в KeyDB (`crm:v1:tg:link:{code}`, GETDEL),
  успешный bind публикует SSE `telegram.linked` (Р-8);
- CRUD правил `notification_rule` (адресация responsible / manager_of_responsible /
  role / user / tg_username / email — data-model.md §9.4);
- `/internal/bot/*` — только сервисная роль `svc_notification` (клиент crm-notification);
  backend резолвит chat_id → app_user и применяет RBAC привязанного пользователя (§9.3).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.db import get_session
from app.core.errors import not_found
from app.core.events import SSE_TELEGRAM_LINKED
from app.core.security import (
    PEOPLE_ROLES,
    get_current_user,
    require_roles,
    require_service_role,
)
from app.core.sse import publish_event
from app.models.service import NotificationRule
from app.models.user import AppUser
from app.modules.crm.deps import check_version
from app.modules.notifications import service
from app.modules.notifications.schemas import (
    BotBindIn,
    MarkReadIn,
    RuleIn,
    RulePatch,
    SettingsIn,
    TestSendIn,
)

router = APIRouter(tags=["notifications"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[AppUser, Depends(get_current_user)]
BotClaims = Depends(require_service_role("svc_notification"))


def _people_roles(user: AppUser) -> list[str]:
    return sorted(r for r in user.roles if r in PEOPLE_ROLES)


# --------------------------------------------------------------------------------------
# Настройки, каналы, тест (§9.1)
# --------------------------------------------------------------------------------------


@router.get("/notifications/settings", summary="Мои настройки каналов и событий")
async def get_settings(session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    return await service.get_user_settings(session, user)


@router.put("/notifications/settings", summary="Изменить настройки нотификаций")
async def put_settings(
    body: SettingsIn, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    return await service.put_user_settings(session, user, body.as_storage())


@router.post("/notifications/test", status_code=202, summary="Тестовая отправка в канал")
async def send_test(
    body: TestSendIn, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    return await service.send_test(session, user, body.channel)


@router.get("/notifications/channels", summary="Каналы доставки и их состояния")
async def channels(user: CurrentUser) -> dict[str, Any]:
    items = []
    for item in service.CHANNELS_CATALOG:
        row = dict(item)
        if row["code"] == "telegram":
            row["linked"] = user.tg_chat_id is not None
        items.append(row)
    return {"items": items}


# --------------------------------------------------------------------------------------
# In-app лента (§9.1)
# --------------------------------------------------------------------------------------


@router.get("/notifications", summary="In-app лента уведомлений")
async def list_notifications(
    session: SessionDep,
    user: CurrentUser,
    unread: Annotated[bool, Query(description="Только непрочитанные")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return await service.list_feed(
        session, user, unread=unread, limit=limit, offset=offset
    )


@router.post("/notifications/read", summary="Отметить уведомления прочитанными")
async def mark_read(
    body: MarkReadIn, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    return await service.mark_read(session, user, ids=body.ids, mark_all=body.all)


# --------------------------------------------------------------------------------------
# Привязка Telegram (§9.2)
# --------------------------------------------------------------------------------------


@router.post(
    "/notifications/telegram/link-code",
    status_code=201,
    summary="Одноразовый код привязки Telegram (6 цифр, TTL 10 мин)",
)
async def telegram_link_code(user: CurrentUser) -> dict[str, Any]:
    return await service.create_link_code(user)


@router.delete("/notifications/telegram/link", summary="Отвязать свой Telegram")
async def telegram_unlink(
    request: Request, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    was_linked = await service.unbind_user(user)
    if was_linked:
        await write_audit(
            session,
            actor_user_id=user.id,
            action="user.telegram_unlinked",
            entity_type="app_user",
            entity_id=user.id,
            ip=client_ip(request),
        )
    await session.commit()
    return {"unlinked": was_linked}


# --------------------------------------------------------------------------------------
# Правила notification_rule (настройка адресации и порогов; §9.4, стадия B4)
# --------------------------------------------------------------------------------------


@router.get(
    "/notifications/rules",
    summary="Правила нотификаций",
    dependencies=[Depends(require_roles("admin", "head_kam"))],
)
async def list_rules(
    session: SessionDep,
    event_type: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(NotificationRule).order_by(
        NotificationRule.event_type, NotificationRule.created_at
    )
    if event_type:
        stmt = stmt.where(NotificationRule.event_type == event_type)
    if is_active is not None:
        stmt = stmt.where(NotificationRule.is_active.is_(is_active))
    rules = (await session.execute(stmt)).scalars().all()
    return {"items": [service.serialize_rule(r) for r in rules], "total": len(rules)}


@router.post("/notifications/rules", status_code=201, summary="Создать правило")
async def create_rule(
    body: RuleIn,
    request: Request,
    session: SessionDep,
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    payload = await service.validate_rule_payload(session, body.model_dump())
    rule = NotificationRule(id=uuid.uuid4(), created_by=user.id, **payload)
    session.add(rule)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="notification_rule.created",
        entity_type="notification_rule",
        entity_id=rule.id,
        after=body.model_dump(mode="json"),
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(rule)
    return service.serialize_rule(rule)


@router.patch("/notifications/rules/{rule_id}", summary="Изменить правило")
async def update_rule(
    rule_id: uuid.UUID,
    body: RulePatch,
    request: Request,
    session: SessionDep,
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    rule = await service.get_rule(session, rule_id)
    if body.version is not None:
        check_version(rule, body.version)
    raw = body.model_dump(exclude_unset=True, exclude={"version"})
    before = service.serialize_rule(rule)
    payload = await service.validate_rule_payload(session, raw, partial=True)
    for field, value in payload.items():
        setattr(rule, field, value)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="notification_rule.updated",
        entity_type="notification_rule",
        entity_id=rule.id,
        before={k: v for k, v in before.items() if k in payload},
        after={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(rule)
    return service.serialize_rule(rule)


@router.delete(
    "/notifications/rules/{rule_id}", status_code=204, summary="Удалить правило"
)
async def delete_rule(
    rule_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> None:
    rule = await service.get_rule(session, rule_id)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="notification_rule.deleted",
        entity_type="notification_rule",
        entity_id=rule.id,
        before=service.serialize_rule(rule),
        ip=client_ip(request),
    )
    await session.delete(rule)
    await session.commit()


# --------------------------------------------------------------------------------------
# Внутренние эндпоинты бота (клиент crm-notification, роль svc_notification; §9.2–9.3)
# --------------------------------------------------------------------------------------


@router.post(
    "/internal/bot/bind",
    summary="Обменять одноразовый код на привязку chat_id",
    dependencies=[BotClaims],
)
async def bot_bind(body: BotBindIn, session: SessionDep) -> dict[str, Any]:
    user_id = await service.consume_link_code(body.code)
    if user_id is None:
        raise not_found("Код неверен или просрочен. Получите новый код в CRM")
    user = await service.bind_chat(
        session, user_id=user_id, chat_id=body.chat_id, tg_username=body.tg_username
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        action="user.telegram_linked",
        entity_type="app_user",
        entity_id=user.id,
        after={"chat_id": body.chat_id, "tg_username": user.tg_username},
    )
    await session.commit()
    await publish_event(
        SSE_TELEGRAM_LINKED,
        {"user_id": str(user.id), "tg_username": user.tg_username},
    )
    return {
        "user_id": str(user.id),
        "full_name": user.full_name,
        "roles": _people_roles(user),
    }


@router.get(
    "/internal/bot/bindings/{chat_id}",
    summary="Кто привязан к chat_id + роли",
    dependencies=[BotClaims],
)
async def bot_binding(chat_id: int, session: SessionDep) -> dict[str, Any]:
    user = await service.user_by_chat_id(session, chat_id)
    return {
        "user_id": str(user.id),
        "username": user.username,
        "full_name": user.full_name,
        "roles": _people_roles(user),
        "linked_at": user.tg_linked_at,
    }


@router.delete(
    "/internal/bot/bindings/{chat_id}",
    summary="Отвязка со стороны бота (/unlink)",
    dependencies=[BotClaims],
)
async def bot_unbind(chat_id: int, session: SessionDep) -> dict[str, Any]:
    user = await service.user_by_chat_id(session, chat_id)
    await service.unbind_user(user)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="user.telegram_unlinked",
        entity_type="app_user",
        entity_id=user.id,
    )
    await session.commit()
    return {"unlinked": True}


@router.get(
    "/internal/bot/users/{chat_id}/summary",
    summary="Команда /status: сводка по RBAC привязанного",
    dependencies=[BotClaims],
)
async def bot_summary(chat_id: int, session: SessionDep) -> dict[str, Any]:
    user = await service.user_by_chat_id(session, chat_id)
    return await service.bot_summary(session, user)


@router.get(
    "/internal/bot/users/{chat_id}/requests",
    summary="Команды /my и /stuck: сокращённые карточки без ПДн",
    dependencies=[BotClaims],
)
async def bot_requests(
    chat_id: int,
    session: SessionDep,
    stuck: Annotated[bool, Query(description="Только зависшие")] = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    user = await service.user_by_chat_id(session, chat_id)
    items = await service.bot_requests(session, user, stuck_only=stuck, limit=limit)
    return {"items": items, "total": len(items)}


@router.get(
    "/internal/bot/users/{chat_id}/notifications",
    summary="Команда /inbox: лента привязанного пользователя",
    dependencies=[BotClaims],
)
async def bot_notifications(
    chat_id: int,
    session: SessionDep,
    unread: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    user = await service.user_by_chat_id(session, chat_id)
    return await service.list_feed(session, user, unread=unread, limit=limit, offset=0)
