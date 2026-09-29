"""Пресеты таблиц/фильтров пользователя (api-contract.md §2.2).

Резолюция по хранению: собственной таблицы в DDL (data-model.md) нет — документ
пресетов пользователя живёт в `app_setting['ui:presets:{user_id}']` (jsonb),
как и остальные «документные» настройки. Объёмы (десятки пресетов на человека)
и однопользовательский доступ делают документ безопасным без блокировок.

Чистые операции над списком вынесены отдельно и юнит-тестируются без БД.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found, validation_error
from app.models.service import AppSetting
from app.models.user import AppUser

PRESETS_KEY_PREFIX = "ui:presets:"
MAX_PRESETS_PER_USER = 100


def presets_key(user_id: uuid.UUID) -> str:
    return f"{PRESETS_KEY_PREFIX}{user_id}"


def filter_presets(items: list[dict[str, Any]], screen: str | None) -> list[dict[str, Any]]:
    if screen is None:
        return items
    return [item for item in items if item.get("screen") == screen]


def add_preset(items: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """Добавляет пресет в список (мутирует его); возвращает созданный элемент."""
    if len(items) >= MAX_PRESETS_PER_USER:
        raise validation_error(
            f"Достигнут лимит пресетов ({MAX_PRESETS_PER_USER}); удалите ненужные"
        )
    preset = {
        "id": str(uuid.uuid4()),
        "screen": payload["screen"],
        "name": payload["name"],
        "is_default": bool(payload.get("is_default", False)),
        "state": payload.get("state") or {},
    }
    if preset["is_default"]:
        for item in items:
            if item.get("screen") == preset["screen"]:
                item["is_default"] = False
    items.append(preset)
    return preset


def patch_preset(
    items: list[dict[str, Any]], preset_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    target = next((item for item in items if item.get("id") == preset_id), None)
    if target is None:
        raise not_found("Пресет не найден")
    if "name" in payload and payload["name"]:
        target["name"] = payload["name"]
    if "state" in payload and payload["state"] is not None:
        target["state"] = payload["state"]
    if "is_default" in payload and payload["is_default"] is not None:
        target["is_default"] = bool(payload["is_default"])
        if target["is_default"]:
            for item in items:
                if item is not target and item.get("screen") == target.get("screen"):
                    item["is_default"] = False
    return target


def remove_preset(items: list[dict[str, Any]], preset_id: str) -> list[dict[str, Any]]:
    filtered = [item for item in items if item.get("id") != preset_id]
    if len(filtered) == len(items):
        raise not_found("Пресет не найден")
    return filtered


# --- привязка к хранилищу ---------------------------------------------------------------


async def load_items(session: AsyncSession, user: AppUser) -> list[dict[str, Any]]:
    setting = await session.get(AppSetting, presets_key(user.id))
    if setting and isinstance(setting.value, dict):
        items = setting.value.get("items")
        if isinstance(items, list):
            return items
    return []


async def save_items(
    session: AsyncSession, user: AppUser, items: list[dict[str, Any]]
) -> None:
    key = presets_key(user.id)
    setting = await session.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(
            key=key, value={"items": items}, description="Пресеты UI пользователя"
        )
        session.add(setting)
    else:
        setting.value = {"items": items}
    setting.updated_by = user.id
    await session.commit()
