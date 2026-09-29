"""Админка: пользователи, системные настройки, фичефлаги, аудит (api-contract.md §10).

Резолюции хранения:
- системные настройки — отдельные ключи `app_setting` (сид data-model.md §9.4),
  API-поле `stuck_threshold_days` ↔ ключ `stuck_threshold_days_default`;
  версия документа настроек — ключ `settings_version`;
- фичефлаги — ключ `app_setting['feature_flags']` (реестр и дефолты — в коде);
- `last_login_at` в списке пользователей — проекция `app_user.updated_at`
  (отдельной колонки в DDL нет; зеркало обновляется при каждом логине).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError, not_found, stale_version, validation_error
from app.core.pagination import ListParams, apply_sort
from app.core.security import PEOPLE_ROLES
from app.models.service import AppSetting, AuditLog
from app.models.user import AppUser

# --- фичефлаги (OVERVIEW.md Р-13, N6, N7; ux.md §14.3) --------------------------------

FEATURE_FLAGS_KEY = "feature_flags"

KNOWN_FLAGS: dict[str, dict[str, Any]] = {
    "auto_ranking": {
        "default": False,
        "label": "Авторанжирование программ по числу заявок (бонус, read-only)",
    },
    "report_pdf": {
        "default": False,
        "label": "PDF-отчёты (вне MVP — Р-13)",
    },
    "llm_features": {
        "default": False,
        "label": "LLM-функции (в закрытом контуре — выключено)",
    },
}

# --- системные настройки (§10.3) -------------------------------------------------------

SETTINGS_VERSION_KEY = "settings_version"

# API-поле → (ключ app_setting, валидатор)
_SETTING_FIELDS: dict[str, str] = {
    "stuck_threshold_days": "stuck_threshold_days_default",
    "stuck_escalate_to_manager": "stuck_escalate_to_manager",
    "integration_mode": "integration_mode",
    "file_delivery": "file_delivery",
}

_SETTING_DEFAULTS: dict[str, Any] = {
    "stuck_threshold_days": 14,
    "stuck_escalate_to_manager": True,
    "integration_mode": "http",
    "file_delivery": "proxy",
}


def validate_system_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Валидация PUT /admin/settings; возвращает только известные поля."""
    result: dict[str, Any] = {}
    if "stuck_threshold_days" in body:
        value = body["stuck_threshold_days"]
        if not isinstance(value, int) or not (1 <= value <= 60):
            raise validation_error("stuck_threshold_days — целое число от 1 до 60")
        result["stuck_threshold_days"] = value
    if "stuck_escalate_to_manager" in body:
        result["stuck_escalate_to_manager"] = bool(body["stuck_escalate_to_manager"])
    if "integration_mode" in body:
        if body["integration_mode"] not in ("http", "stream"):
            raise validation_error("integration_mode — «http» или «stream»")
        result["integration_mode"] = body["integration_mode"]
    if "file_delivery" in body:
        if body["file_delivery"] not in ("proxy", "presigned"):
            raise validation_error("file_delivery — «proxy» или «presigned»")
        result["file_delivery"] = body["file_delivery"]
    return result


async def _setting_value(session: AsyncSession, key: str, default: Any) -> Any:
    setting = await session.get(AppSetting, key)
    if setting and isinstance(setting.value, dict) and "value" in setting.value:
        return setting.value["value"]
    return default


async def _put_setting(
    session: AsyncSession, key: str, value: Any, updated_by: uuid.UUID | None
) -> None:
    setting = await session.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value={"value": value})
        session.add(setting)
    else:
        setting.value = {"value": value}
    setting.updated_by = updated_by


async def get_system_settings(session: AsyncSession) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, key in _SETTING_FIELDS.items():
        result[field] = await _setting_value(session, key, _SETTING_DEFAULTS[field])
    result["version"] = int(await _setting_value(session, SETTINGS_VERSION_KEY, 1))
    return result


async def put_system_settings(
    session: AsyncSession, user: AppUser, body: dict[str, Any]
) -> dict[str, Any]:
    current_version = int(await _setting_value(session, SETTINGS_VERSION_KEY, 1))
    provided = body.get("version")
    if provided is not None and provided != current_version:
        raise stale_version()
    validated = validate_system_settings(body)
    for field, value in validated.items():
        await _put_setting(session, _SETTING_FIELDS[field], value, user.id)
    await _put_setting(session, SETTINGS_VERSION_KEY, current_version + 1, user.id)
    return await get_system_settings(session)


# --- фичефлаги -------------------------------------------------------------------------


def merge_flags(stored: dict[str, Any] | None) -> dict[str, bool]:
    stored = stored or {}
    return {
        name: bool(stored.get(name, spec["default"]))
        for name, spec in KNOWN_FLAGS.items()
    }


async def get_flags(session: AsyncSession) -> dict[str, bool]:
    setting = await session.get(AppSetting, FEATURE_FLAGS_KEY)
    return merge_flags(setting.value if setting else None)


async def set_flag(
    session: AsyncSession, user: AppUser, name: str, enabled: bool
) -> dict[str, Any]:
    if name not in KNOWN_FLAGS:
        raise not_found(
            f"Фичефлаг «{name}» не найден. Доступно: {', '.join(sorted(KNOWN_FLAGS))}"
        )
    setting = await session.get(AppSetting, FEATURE_FLAGS_KEY)
    flags = merge_flags(setting.value if setting else None)
    flags[name] = enabled
    if setting is None:
        setting = AppSetting(
            key=FEATURE_FLAGS_KEY, value=flags, description="Фичефлаги приложения"
        )
        session.add(setting)
    else:
        setting.value = flags
    setting.updated_by = user.id
    return {"name": name, "enabled": enabled, "label": KNOWN_FLAGS[name]["label"]}


def flags_catalog(flags: dict[str, bool]) -> list[dict[str, Any]]:
    return [
        {"name": name, "enabled": flags[name], "label": spec["label"]}
        for name, spec in KNOWN_FLAGS.items()
    ]


# --- пользователи (§10.2) --------------------------------------------------------------


def serialize_user(user: AppUser) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "roles": sorted(r for r in user.roles if r in PEOPLE_ROLES),
        "manager_id": str(user.manager_id) if user.manager_id else None,
        "telegram_linked": user.tg_chat_id is not None,
        "tg_username": user.tg_username,
        "is_active": user.is_active,
        "last_login_at": user.updated_at,
        "version": user.xmin,
    }


async def list_users(
    session: AsyncSession,
    *,
    role: str | None,
    search: str | None,
    params: ListParams,
) -> tuple[list[dict[str, Any]], int]:
    stmt = select(AppUser)
    if role:
        stmt = stmt.where(AppUser.roles.any(role))
    if search:
        needle = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                AppUser.username.ilike(needle),
                AppUser.full_name.ilike(needle),
                AppUser.email.ilike(needle),
            )
        )
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(
        stmt,
        params.sort,
        {
            "username": AppUser.username,
            "full_name": AppUser.full_name,
            "email": AppUser.email,
            "created_at": AppUser.created_at,
        },
        default="username",
    )
    rows = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset)))
        .scalars()
        .all()
    )
    return [serialize_user(u) for u in rows], int(total)


async def set_manager(
    session: AsyncSession, user_id: uuid.UUID, manager_id: uuid.UUID | None
) -> tuple[AppUser, uuid.UUID | None]:
    """Назначение руководителя для эскалаций; защита от самоссылки и цикла."""
    user = await session.get(AppUser, user_id)
    if user is None:
        raise not_found("Пользователь не найден")
    previous = user.manager_id
    if manager_id is not None:
        if manager_id == user_id:
            raise validation_error("Пользователь не может быть руководителем самого себя")
        manager = await session.get(AppUser, manager_id)
        if manager is None or not manager.is_active:
            raise not_found("Руководитель не найден или деактивирован")
        # подъём по цепочке руководителей: назначение не должно замыкать цикл
        cursor: AppUser | None = manager
        for _ in range(32):
            if cursor is None or cursor.manager_id is None:
                break
            if cursor.manager_id == user_id:
                raise validation_error(
                    "Назначение создаёт цикл в иерархии руководителей"
                )
            cursor = await session.get(AppUser, cursor.manager_id)
    user.manager_id = manager_id
    return user, previous


# --- синхронизация из Keycloak Admin API (§10.2) ---------------------------------------


async def sync_users_from_keycloak(session: AsyncSession) -> dict[str, int]:
    """Принудительный upsert зеркала app_user из Keycloak Admin API.

    Требует confidential-клиента с service-account ролью `view-users`
    (env `KEYCLOAK_ADMIN_CLIENT_ID`/`KEYCLOAK_ADMIN_CLIENT_SECRET`). Штатный путь
    синхронизации — upsert при логине (data-model.md §3); этот эндпоинт — для
    предзаполнения списка до первого входа пользователей.
    """
    settings = get_settings()
    if not settings.keycloak_admin_client_id or not settings.keycloak_admin_client_secret:
        raise ApiError(
            502,
            "integration_unavailable",
            "Синхронизация с Keycloak не настроена: задайте "
            "KEYCLOAK_ADMIN_CLIENT_ID и KEYCLOAK_ADMIN_CLIENT_SECRET",
        )
    base = settings.keycloak_internal_url.rstrip("/")
    realm = settings.keycloak_realm
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_response = await client.post(
                f"{base}/realms/{realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.keycloak_admin_client_id,
                    "client_secret": settings.keycloak_admin_client_secret,
                },
            )
            token_response.raise_for_status()
            headers = {
                "Authorization": f"Bearer {token_response.json()['access_token']}"
            }
            users_response = await client.get(
                f"{base}/admin/realms/{realm}/users",
                params={"max": 500, "briefRepresentation": "false"},
                headers=headers,
            )
            users_response.raise_for_status()
            kc_users = users_response.json()

            created = updated = 0
            for kc_user in kc_users:
                roles_response = await client.get(
                    f"{base}/admin/realms/{realm}/users/{kc_user['id']}"
                    "/role-mappings/realm",
                    headers=headers,
                )
                roles_response.raise_for_status()
                roles = sorted(
                    {r["name"] for r in roles_response.json()} & set(PEOPLE_ROLES)
                )
                if not roles:
                    continue  # сервисные аккаунты и технические записи не зеркалим
                sub = uuid.UUID(kc_user["id"])
                full_name = " ".join(
                    part
                    for part in (kc_user.get("firstName"), kc_user.get("lastName"))
                    if part
                ) or None
                user = (
                    await session.execute(
                        select(AppUser).where(AppUser.keycloak_sub == sub)
                    )
                ).scalar_one_or_none()
                if user is None:
                    user = (
                        await session.execute(
                            select(AppUser).where(
                                func.lower(AppUser.username)
                                == str(kc_user.get("username", "")).lower()
                            )
                        )
                    ).scalar_one_or_none()
                if user is None:
                    session.add(
                        AppUser(
                            keycloak_sub=sub,
                            username=kc_user["username"],
                            email=kc_user.get("email"),
                            full_name=full_name,
                            roles=roles,
                            is_active=bool(kc_user.get("enabled", True)),
                        )
                    )
                    created += 1
                else:
                    user.keycloak_sub = sub
                    user.email = kc_user.get("email") or user.email
                    user.full_name = full_name or user.full_name
                    user.roles = roles
                    user.is_active = bool(kc_user.get("enabled", True))
                    updated += 1
            await session.commit()
            return {"synced": created + updated, "created": created, "updated": updated}
    except httpx.HTTPError as exc:
        raise ApiError(
            502,
            "integration_unavailable",
            f"Keycloak Admin API недоступен: {exc}",
        ) from exc


# --- аудит (§10.4) ---------------------------------------------------------------------


async def list_audit(
    session: AsyncSession,
    *,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    action: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    params: ListParams,
) -> tuple[list[dict[str, Any]], int]:
    stmt = select(AuditLog, AppUser).outerjoin(
        AppUser, AppUser.id == AuditLog.actor_user_id
    )
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.created_at <= date_to)
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(params.limit)
            .offset(params.offset)
        )
    ).all()
    items = [
        {
            "id": entry.id,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": str(entry.entity_id) if entry.entity_id else None,
            "actor": (
                {"id": str(actor.id), "username": actor.username,
                 "full_name": actor.full_name}
                if actor
                else None
            ),
            "before": entry.before,
            "after": entry.after,
            "ip": str(entry.ip) if entry.ip else None,
            "created_at": entry.created_at,
        }
        for entry, actor in rows
    ]
    return items, int(total)
