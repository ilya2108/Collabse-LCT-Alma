"""Персональные настройки доставки нотификаций (api-contract.md §9.1).

Резолюция по хранению: отдельной таблицы настроек в DDL (data-model.md §9.4) нет,
поэтому документ настроек живёт в `app_setting['notify:settings:{user_id}']`
(jsonb, тот же приём, что и справочники-списки `dict:*`). Дефолты — в коде,
хранится только то, что пользователь менял; чтение всегда мержит поверх дефолтов.

Модуль намеренно не импортирует `app.core.outbox` (outbox импортирует его сам,
когда фильтрует получателей) — только модели и реестр событий.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import NOTIFICATION_EVENT_TYPES
from app.models.service import AppSetting
from app.models.user import AppUser

SETTINGS_KEY_PREFIX = "notify:settings:"
READ_KEY_PREFIX = "notify:read:"

CHANNELS = ("in_app", "telegram", "email", "max")
# каналы физической доставки (CHECK в DDL `notification.channel`); in_app — только лента
DELIVERY_CHANNELS = ("telegram", "email", "max")

# события, у которых есть флаг only_mine («только по моим заявкам», §9.1)
ONLY_MINE_EVENTS = frozenset({"request.transitioned", "request.comment_added"})

DEFAULT_CHANNELS: dict[str, dict[str, Any]] = {
    "in_app": {"enabled": True},
    "telegram": {"enabled": True},
    "email": {"enabled": True, "address": None},
    "max": {"enabled": False, "address": None},
}


def default_events() -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for event_type in sorted(NOTIFICATION_EVENT_TYPES):
        item: dict[str, Any] = {"enabled": True}
        if event_type in ONLY_MINE_EVENTS:
            item["only_mine"] = True
        events[event_type] = item
    return events


def settings_key(user_id: uuid.UUID) -> str:
    return f"{SETTINGS_KEY_PREFIX}{user_id}"


def read_marker_key(user_id: uuid.UUID) -> str:
    return f"{READ_KEY_PREFIX}{user_id}"


def merge_settings(stored: dict[str, Any] | None, user: AppUser | None = None) -> dict[str, Any]:
    """Полный документ настроек: дефолты + сохранённые пользователем изменения.

    Неизвестные каналы/события из хранилища отбрасываются (защита от мусора
    после смены реестра событий).
    """
    stored = stored or {}
    channels = copy.deepcopy(DEFAULT_CHANNELS)
    for code, patch in (stored.get("channels") or {}).items():
        if code in channels and isinstance(patch, dict):
            if "enabled" in patch:
                channels[code]["enabled"] = bool(patch["enabled"])
            if "address" in patch and "address" in channels[code]:
                address = patch["address"]
                channels[code]["address"] = str(address) if address else None
    if user is not None and not channels["email"]["address"]:
        channels["email"]["address"] = user.email
    channels["telegram"]["linked"] = bool(user is not None and user.tg_chat_id)

    events = default_events()
    for event_type, patch in (stored.get("events") or {}).items():
        if event_type in events and isinstance(patch, dict):
            if "enabled" in patch:
                events[event_type]["enabled"] = bool(patch["enabled"])
            if "only_mine" in patch and event_type in ONLY_MINE_EVENTS:
                events[event_type]["only_mine"] = bool(patch["only_mine"])

    version = stored.get("version")
    return {
        "channels": channels,
        "events": events,
        "version": version if isinstance(version, int) and version >= 1 else 1,
    }


async def load_settings(session: AsyncSession, user: AppUser) -> dict[str, Any]:
    setting = await session.get(AppSetting, settings_key(user.id))
    return merge_settings(setting.value if setting else None, user)


async def load_prefs_for_users(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Настройки нескольких получателей одним обращением (для outbox-фильтра)."""
    result: dict[uuid.UUID, dict[str, Any]] = {}
    for user_id in user_ids:
        setting = await session.get(AppSetting, settings_key(user_id))
        result[user_id] = merge_settings(setting.value if setting else None)
    return result


def delivery_decision(
    prefs: dict[str, Any] | None,
    *,
    event_type: str,
    channel: str,
    is_responsible: bool | None,
) -> str:
    """Решение по получателю: 'deliver' | 'skip_channel' | 'drop'.

    - событие выключено или `only_mine` не совпало → 'drop' (строка не создаётся);
    - канал доставки выключен → 'skip_channel' (строка со статусом skipped —
      остаётся в in-app ленте, наружу не уходит);
    - иначе 'deliver'.
    """
    if prefs is None:
        return "deliver"
    event = (prefs.get("events") or {}).get(event_type) or {}
    if event.get("enabled") is False:
        return "drop"
    if event.get("only_mine") and is_responsible is False:
        return "drop"
    channel_prefs = (prefs.get("channels") or {}).get(channel) or {}
    if channel_prefs.get("enabled") is False:
        return "skip_channel"
    return "deliver"
