"""Нотификации: настройки, in-app лента, привязка Telegram, правила, данные для бота.

Контракты: api-contract.md §9 (настройки/лента/link-code/бот), data-model.md §9.4
(notification_rule, notification, app_setting), Р-8 (код 6 цифр, KeyDB GETDEL),
Р-9 (получатели вычисляются backend'ом — app.core.outbox).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_key, get_cache
from app.core.config import get_settings
from app.core.errors import (
    ApiError,
    conflict,
    not_found,
    stale_version,
    validation_error,
)
from app.core.events import (
    EVENT_NOTIFICATION_TEST,
    NOTIFICATION_EVENT_TYPES,
)
from app.core.outbox import _address_for
from app.models.deal import Deal
from app.models.service import AppSetting, Notification, NotificationRule
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowChangeRequest, WorkflowStage
from app.modules.notifications import prefs as prefs_module
from app.modules.workflow.service import STUCK_DEFAULT_THRESHOLD_DAYS

TG_LINK_CODE_TTL_SECONDS = 600  # 10 минут (Р-8)
TG_LINK_KEY_PREFIX = "tg:link:"
READ_IDS_LIMIT = 500  # маркеры прочтения не растут бесконечно

ROLE_LABELS = {
    "admin": "Администратор",
    "head_kam": "Руководитель КАМов",
    "kam": "КАМ",
    "observer": "Наблюдатель",
}

CHANNELS_CATALOG = [
    {"code": "in_app", "name": "В приложении", "status": "active"},
    {"code": "telegram", "name": "Telegram", "status": "active"},
    {"code": "email", "name": "Email (Exchange)", "status": "stub"},
    {"code": "max", "name": "Max", "status": "stub"},
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Настройки пользователя (§9.1)
# --------------------------------------------------------------------------------------


async def get_user_settings(session: AsyncSession, user: AppUser) -> dict[str, Any]:
    return await prefs_module.load_settings(session, user)


async def put_user_settings(
    session: AsyncSession, user: AppUser, body: dict[str, Any]
) -> dict[str, Any]:
    key = prefs_module.settings_key(user.id)
    setting = await session.get(AppSetting, key)
    current = prefs_module.merge_settings(setting.value if setting else None, user)
    provided_version = body.get("version")
    if provided_version is not None and provided_version != current["version"]:
        raise stale_version()

    stored: dict[str, Any] = {"channels": {}, "events": {}}
    for code, patch in (body.get("channels") or {}).items():
        if code not in prefs_module.CHANNELS or not isinstance(patch, dict):
            raise validation_error(f"Неизвестный канал нотификаций «{code}»")
        item: dict[str, Any] = {"enabled": bool(patch.get("enabled", True))}
        if "address" in patch and code in ("email", "max"):
            item["address"] = str(patch["address"]) if patch.get("address") else None
        stored["channels"][code] = item
    for event_type, patch in (body.get("events") or {}).items():
        if event_type not in NOTIFICATION_EVENT_TYPES or not isinstance(patch, dict):
            raise validation_error(f"Неизвестное событие «{event_type}»")
        item = {"enabled": bool(patch.get("enabled", True))}
        if "only_mine" in patch:
            item["only_mine"] = bool(patch["only_mine"])
        stored["events"][event_type] = item
    stored["version"] = current["version"] + 1

    if setting is None:
        setting = AppSetting(
            key=key, value=stored, description="Настройки нотификаций пользователя"
        )
        session.add(setting)
    else:
        setting.value = stored
    setting.updated_by = user.id
    await session.commit()
    return prefs_module.merge_settings(stored, user)


# --------------------------------------------------------------------------------------
# In-app лента и маркеры прочтения (§9.1)
# --------------------------------------------------------------------------------------


async def _read_marker(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    setting = await session.get(AppSetting, prefs_module.read_marker_key(user_id))
    value = setting.value if setting and isinstance(setting.value, dict) else {}
    return {
        "read_ids": [str(i) for i in (value.get("read_ids") or [])],
        "all_before": value.get("all_before"),
    }


def _is_read(row: Notification, marker: dict[str, Any]) -> bool:
    all_before = marker.get("all_before")
    if all_before:
        try:
            boundary = datetime.fromisoformat(all_before)
            if row.created_at and row.created_at <= boundary:
                return True
        except ValueError:
            pass
    return str(row.id) in marker["read_ids"]


def serialize_feed_item(row: Notification, *, read: bool) -> dict[str, Any]:
    # форма ленты — frontend NotificationFeedItem: text/url/is_read;
    # body/read/subject оставлены для обратной совместимости потребителей
    payload = row.payload or {}
    return {
        "id": str(row.id),
        "event": row.event_type,
        "entity_type": row.entity_type,
        "entity_id": str(row.entity_id) if row.entity_id else None,
        "channel": row.channel,
        "subject": row.subject,
        "text": row.body,
        "body": row.body,
        "url": payload.get("url"),
        "payload": row.payload,
        "status": row.status,
        "is_read": read,
        "read": read,
        "created_at": row.created_at,
    }


async def list_feed(
    session: AsyncSession,
    user: AppUser,
    *,
    unread: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    marker = await _read_marker(session, user.id)
    base = select(Notification).where(Notification.recipient_user_id == user.id)
    rows = list(
        (
            await session.execute(
                base.order_by(Notification.created_at.desc()).limit(1000)
            )
        )
        .scalars()
        .all()
    )
    annotated = [(row, _is_read(row, marker)) for row in rows]
    unread_total = sum(1 for _, read in annotated if not read)
    if unread:
        annotated = [(row, read) for row, read in annotated if not read]
    total = len(annotated)
    page = annotated[offset : offset + limit]
    return {
        "items": [serialize_feed_item(row, read=read) for row, read in page],
        "total": total,
        "unread_total": unread_total,
        "limit": limit,
        "offset": offset,
    }


async def mark_read(
    session: AsyncSession,
    user: AppUser,
    *,
    ids: list[uuid.UUID] | None,
    mark_all: bool,
) -> dict[str, Any]:
    key = prefs_module.read_marker_key(user.id)
    setting = await session.get(AppSetting, key)
    marker = await _read_marker(session, user.id)
    if mark_all:
        marker = {"read_ids": [], "all_before": _now().isoformat()}
    else:
        merged = list(dict.fromkeys(marker["read_ids"] + [str(i) for i in (ids or [])]))
        marker["read_ids"] = merged[-READ_IDS_LIMIT:]
    if setting is None:
        setting = AppSetting(
            key=key, value=marker, description="Маркеры прочтения нотификаций"
        )
        session.add(setting)
    else:
        setting.value = marker
    setting.updated_by = user.id
    await session.commit()
    return {"read": True}


# --------------------------------------------------------------------------------------
# Привязка Telegram: one-time code в KeyDB (§9.2, Р-8)
# --------------------------------------------------------------------------------------


def _keydb_unavailable() -> ApiError:
    return ApiError(
        503,
        "service_unavailable",
        "Хранилище одноразовых кодов недоступно, повторите позже",
    )


async def create_link_code(user: AppUser) -> dict[str, Any]:
    """Код 6 цифр, TTL 10 мин, KeyDB `crm:v1:tg:link:{code}` → user_id.

    Повторный запрос инвалидирует предыдущий код пользователя (обратный ключ
    `tg:link:user:{user_id}` хранит последний выданный код).
    """
    client = get_cache().client
    reverse_key = cache_key(f"{TG_LINK_KEY_PREFIX}user:{user.id}")
    try:
        previous = await client.getdel(reverse_key)
        if previous:
            await client.delete(
                cache_key(f"{TG_LINK_KEY_PREFIX}{previous.decode('utf-8', 'ignore')}")
            )
        code = ""
        for _ in range(10):  # коллизия шестизначного кода — маловероятна, но конечна
            candidate = f"{secrets.randbelow(1_000_000):06d}"
            stored = await client.set(
                cache_key(f"{TG_LINK_KEY_PREFIX}{candidate}"),
                str(user.id),
                nx=True,
                ex=TG_LINK_CODE_TTL_SECONDS,
            )
            if stored:
                code = candidate
                break
        if not code:
            raise _keydb_unavailable()
        await client.set(reverse_key, code, ex=TG_LINK_CODE_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        raise _keydb_unavailable() from exc

    settings = get_settings()
    deep_link = (
        f"https://t.me/{settings.telegram_bot_username}?start={code}"
        if settings.telegram_bot_username
        else None
    )
    return {
        "code": code,
        "deep_link": deep_link,
        "expires_at": _now() + timedelta(seconds=TG_LINK_CODE_TTL_SECONDS),
    }


async def consume_link_code(code: str) -> uuid.UUID | None:
    """Атомарное чтение-удаление кода (`GETDEL`); None — код неверен/просрочен."""
    client = get_cache().client
    try:
        raw = await client.getdel(cache_key(f"{TG_LINK_KEY_PREFIX}{code}"))
    except (RedisError, OSError) as exc:
        raise _keydb_unavailable() from exc
    if raw is None:
        return None
    try:
        return uuid.UUID(raw.decode("utf-8"))
    except ValueError:
        return None


async def bind_chat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    chat_id: int,
    tg_username: str | None,
) -> AppUser:
    """Upsert привязки в `app_user` (отдельной таблицы нет — data-model.md §3)."""
    user = await session.get(AppUser, user_id)
    if user is None or not user.is_active:
        raise not_found("Пользователь не найден или деактивирован")
    occupied = (
        await session.execute(
            select(AppUser).where(AppUser.tg_chat_id == chat_id, AppUser.id != user_id)
        )
    ).scalar_one_or_none()
    if occupied is not None:
        raise conflict(
            "Этот Telegram-аккаунт уже привязан к другому пользователю. "
            "Сначала выполните /unlink"
        )
    user.tg_chat_id = chat_id
    user.tg_username = (tg_username or "").lstrip("@") or None
    user.tg_linked_at = _now()
    return user


async def unbind_user(user: AppUser) -> bool:
    """Сбрасывает привязку; True — она существовала."""
    was_linked = user.tg_chat_id is not None
    user.tg_chat_id = None
    user.tg_username = None
    user.tg_linked_at = None
    return was_linked


async def user_by_chat_id(session: AsyncSession, chat_id: int) -> AppUser:
    user = (
        await session.execute(select(AppUser).where(AppUser.tg_chat_id == chat_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise not_found("Чат не привязан. Получите код привязки в CRM и отправьте /start <код>")
    return user


# --------------------------------------------------------------------------------------
# Тестовая отправка (§9.1)
# --------------------------------------------------------------------------------------


async def send_test(
    session: AsyncSession, user: AppUser, channel: str
) -> dict[str, Any]:
    if channel not in prefs_module.DELIVERY_CHANNELS:
        raise validation_error(
            f"Канал «{channel}» не поддерживает тестовую отправку. "
            f"Доступно: {', '.join(prefs_module.DELIVERY_CHANNELS)}"
        )
    address = _address_for(user, channel)
    if channel == "telegram" and not address:
        raise validation_error(
            "Telegram не привязан. Сначала получите код привязки и отправьте его боту"
        )
    if channel in ("email", "max") and not address:
        raise validation_error("У профиля не указан email — тестовая отправка невозможна")
    row = Notification(
        id=uuid.uuid4(),
        event_type=EVENT_NOTIFICATION_TEST,
        entity_type="app_user",
        entity_id=user.id,
        recipient_user_id=user.id,
        channel=channel,
        address=address,
        subject="Проверка связи",
        body="Проверка связи: канал нотификаций CRM настроен корректно.",
        payload={"channel": channel},
        status="pending",
    )
    notification_id = row.id
    session.add(row)
    await session.commit()
    return {"accepted": True, "notification_id": str(notification_id), "channel": channel}


# --------------------------------------------------------------------------------------
# CRUD правил notification_rule (стадия B4; ролевая модель — admin)
# --------------------------------------------------------------------------------------

RECIPIENT_TYPES = (
    "responsible",
    "manager_of_responsible",
    "role",
    "user",
    "tg_username",
    "email",
)


async def validate_rule_payload(
    session: AsyncSession, payload: dict[str, Any], *, partial: bool = False
) -> dict[str, Any]:
    """Валидация по CHECK-ограничениям DDL (data-model.md §9.4) с русскими ошибками."""
    result: dict[str, Any] = {}
    if "event_type" in payload or not partial:
        event_type = payload.get("event_type")
        if event_type not in NOTIFICATION_EVENT_TYPES:
            raise validation_error(
                f"Неизвестное событие «{event_type}». "
                f"Доступно: {', '.join(sorted(NOTIFICATION_EVENT_TYPES))}"
            )
        result["event_type"] = event_type
    if "channel" in payload or not partial:
        channel = payload.get("channel")
        if channel not in prefs_module.DELIVERY_CHANNELS:
            raise validation_error(
                f"Недопустимый канал «{channel}». "
                f"Доступно: {', '.join(prefs_module.DELIVERY_CHANNELS)}"
            )
        result["channel"] = channel
    if "recipient_type" in payload or not partial:
        recipient_type = payload.get("recipient_type")
        if recipient_type not in RECIPIENT_TYPES:
            raise validation_error(
                f"Недопустимый тип получателя «{recipient_type}». "
                f"Доступно: {', '.join(RECIPIENT_TYPES)}"
            )
        result["recipient_type"] = recipient_type
        value = payload.get("recipient_value")
        if recipient_type in ("responsible", "manager_of_responsible"):
            result["recipient_value"] = None
        else:
            if not value:
                raise validation_error(
                    f"Для типа получателя «{recipient_type}» обязательно поле recipient_value"
                )
            value = str(value).strip()
            if recipient_type == "role":
                if value not in ROLE_LABELS:
                    raise validation_error(
                        f"Неизвестная роль «{value}». Доступно: {', '.join(ROLE_LABELS)}"
                    )
            elif recipient_type == "user":
                try:
                    target = await session.get(AppUser, uuid.UUID(value))
                except ValueError:
                    target = None
                if target is None:
                    raise validation_error("Пользователь-получатель не найден")
            elif recipient_type == "tg_username":
                value = "@" + value.lstrip("@")
            elif recipient_type == "email" and "@" not in value:
                raise validation_error("Некорректный email получателя")
            result["recipient_value"] = value
    if "params" in payload:
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise validation_error("Поле params должно быть объектом")
        threshold = params.get("threshold_days")
        if threshold is not None and not (
            isinstance(threshold, int) and 1 <= threshold <= 60
        ):
            raise validation_error("params.threshold_days — целое число от 1 до 60")
        repeat = params.get("repeat_every_days")
        if repeat is not None and not (isinstance(repeat, int) and repeat >= 1):
            raise validation_error("params.repeat_every_days — целое число не меньше 1")
        result["params"] = params
    for field in ("workflow_id", "stage_id"):
        if field in payload:
            result[field] = payload[field]
    if "is_active" in payload:
        result["is_active"] = bool(payload["is_active"])
    return result


def serialize_rule(rule: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "event_type": rule.event_type,
        "workflow_id": str(rule.workflow_id) if rule.workflow_id else None,
        "stage_id": str(rule.stage_id) if rule.stage_id else None,
        "channel": rule.channel,
        "recipient_type": rule.recipient_type,
        "recipient_value": rule.recipient_value,
        "params": rule.params or {},
        "is_active": rule.is_active,
        "version": rule.xmin,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


async def get_rule(session: AsyncSession, rule_id: uuid.UUID) -> NotificationRule:
    rule = await session.get(NotificationRule, rule_id)
    if rule is None:
        raise not_found("Правило нотификаций не найдено")
    return rule


# --------------------------------------------------------------------------------------
# Данные для команд бота (§9.3): RBAC привязанного пользователя, без ПДн студентов
# --------------------------------------------------------------------------------------


async def _global_stuck_default(session: AsyncSession) -> int:
    setting = await session.get(AppSetting, "stuck_threshold_days_default")
    if setting and isinstance(setting.value, dict):
        value = setting.value.get("value")
        if isinstance(value, int) and 1 <= value <= 60:
            return value
    return STUCK_DEFAULT_THRESHOLD_DAYS


def _stuck_condition(global_default: int):
    """SQL-условие «заявка зависла» по каскаду порога stage → workflow → глобальный."""
    return func.now() - Deal.stage_entered_at > func.make_interval(
        0,
        0,
        0,
        func.coalesce(
            WorkflowStage.stuck_threshold_days,
            Workflow.stuck_threshold_days,
            global_default,
        ),
    )


def _bot_scope_is_all(user: AppUser, *, stuck_only: bool) -> bool:
    """Зона выдачи бота: «мои» — всегда свои; зависшие для head_kam/admin/observer — все."""
    if not stuck_only:
        return False
    return bool(set(user.roles) & {"head_kam", "admin", "observer"})


async def bot_summary(session: AsyncSession, user: AppUser) -> dict[str, Any]:
    is_manager = bool(set(user.roles) & {"head_kam", "admin"})
    global_default = await _global_stuck_default(session)

    my_open = select(func.count()).select_from(Deal).where(
        Deal.status == "open", Deal.responsible_user_id == user.id
    )
    active_requests = (await session.execute(my_open)).scalar_one()

    stuck_stmt = (
        select(func.count())
        .select_from(Deal)
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(
            Deal.status == "open",
            WorkflowStage.is_terminal.is_(False),
            _stuck_condition(global_default),
        )
    )
    if not is_manager and "observer" not in user.roles:
        stuck_stmt = stuck_stmt.where(Deal.responsible_user_id == user.id)
    stuck = (await session.execute(stuck_stmt)).scalar_one()

    waiting_approval = 0
    if is_manager:
        waiting_approval = (
            await session.execute(
                select(func.count())
                .select_from(WorkflowChangeRequest)
                .where(WorkflowChangeRequest.status == "pending")
            )
        ).scalar_one()

    return {
        "active_requests": int(active_requests),
        "stuck": int(stuck),
        "waiting_approval": int(waiting_approval),
        "role_labels": [ROLE_LABELS[r] for r in sorted(user.roles) if r in ROLE_LABELS],
    }


async def bot_requests(
    session: AsyncSession, user: AppUser, *, stuck_only: bool, limit: int
) -> list[dict[str, Any]]:
    """Сокращённые карточки заявок для /my и /stuck — без ПДн (только title/status)."""
    global_default = await _global_stuck_default(session)
    stmt = (
        select(Deal, WorkflowStage, Workflow)
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(Deal.status == "open")
    )
    if not _bot_scope_is_all(user, stuck_only=stuck_only):
        stmt = stmt.where(Deal.responsible_user_id == user.id)
    if stuck_only:
        stmt = stmt.where(
            WorkflowStage.is_terminal.is_(False), _stuck_condition(global_default)
        )
        stmt = stmt.order_by(Deal.stage_entered_at.asc())
    else:
        stmt = stmt.order_by(Deal.created_at.desc())
    rows = (await session.execute(stmt.limit(limit))).all()

    now = _now()
    items = []
    for deal, stage, workflow in rows:
        days = max((now - deal.stage_entered_at).days, 0)
        threshold = (
            stage.stuck_threshold_days
            if stage.stuck_threshold_days is not None
            else (
                workflow.stuck_threshold_days
                if workflow.stuck_threshold_days is not None
                else global_default
            )
        )
        items.append(
            {
                "id": str(deal.id),
                "title": deal.title,
                "workflow_type": deal.pipeline,
                "status": {
                    "id": str(stage.id),
                    "code": stage.code,
                    "name": stage.name,
                    "color": stage.color,
                },
                "stuck_days": days,
                "is_stuck": deal.status == "open"
                and not stage.is_terminal
                and days > threshold,
            }
        )
    return items
