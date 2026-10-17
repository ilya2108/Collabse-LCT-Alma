"""Роли и права: CRUD ролей, назначение ролей пользователям, защита от потери управления.

Правила (Р-F2-2, Р-F2-9, Р-F2-12):
- все мутации RBAC — в одной транзакции под `pg_advisory_xact_lock(hashtextextended(
  'rbac', 0))`: двое администраторов не снимут друг с друга права одновременно;
- код роли неизменяем, назначаются только существующие коды; роль нельзя удалить,
  пока она назначена или указана в переходах процессов (409 `role_in_use`);
- admin — системная заблокированная роль: права и область не меняются;
- после любого изменения остаётся хотя бы один активный пользователь с `admin:roles`,
  и снять это право с себя нельзя (409 `access_lockout`);
- после commit — сброс кэша каталога и SSE `access.changed` (`after_rbac_change`):
  реплики сбрасывают кэш, фронты перечитывают /auth/me без перезахода.

Порядок локов при удалении роли: RBAC → workflow b2b → workflow b2c (эксклюзивные
advisory-локи воронок из `workflow/locks.py`; применение пакетов схемы RBAC-лок не берёт) —
дедлок исключён. Применение пакета читает роли свежим SELECT уже под локом воронки, поэтому
роль не попадёт в переходы в момент удаления. Ожидание локов воронок ограничено
`APPLY_LOCK_TIMEOUT_MS`: дольше — 409 `workflow_busy` с `Retry-After`, роль не удалена.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ensure_role_catalog, reset_role_catalog
from app.core.audit import write_audit
from app.core.errors import ApiError, not_found, validation_error
from app.core.events import SSE_ACCESS_CHANGED
from app.core.rbac_catalog import (
    PERMISSION_CODES,
    PERMISSION_GROUPS,
    PERMISSIONS,
    SYSTEM_ROLE_CODES,
    SYSTEM_ROLES,
    WORKFLOW_TYPE_LABELS,
    WORKFLOW_TYPES,
    missing_requirements,
    permission_name,
)
from app.core.sse import publish_event
from app.models.access import AppRole, AppRolePermission
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowTransition
from app.modules.crm.deps import check_version
from app.modules.workflow.locks import acquire_exclusive_workflow_lock, lock_timeout_as_busy

ADMIN_ROLES_PERMISSION = "admin:roles"
_PERMISSION_ORDER = {definition.code: index for index, definition in enumerate(PERMISSIONS)}

SELF_LOCKOUT_MESSAGE = (
    "Нельзя снять с себя право управления ролями — попросите другого администратора"
)
LAST_ADMIN_MESSAGE = (
    "Должен остаться хотя бы один активный пользователь с правом "
    f"«{permission_name(ADMIN_ROLES_PERMISSION)}»"
)


def _conflict(code: str, message: str, details: list[dict[str, Any]] | None = None) -> ApiError:
    return ApiError(409, code, message, details)


def _sorted_permissions(codes: Iterable[str]) -> list[str]:
    return sorted(set(codes), key=lambda code: _PERMISSION_ORDER.get(code, 10_000))


def _sorted_types(values: Iterable[str]) -> list[str]:
    selected = set(values)
    return [wf for wf in WORKFLOW_TYPES if wf in selected]


# --------------------------------------------------------------------------------------
# Лок и чистые проверки
# --------------------------------------------------------------------------------------


async def acquire_rbac_lock(session: AsyncSession) -> None:
    """Транзакционный advisory-лок всех изменений RBAC (снимается на COMMIT/ROLLBACK)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('rbac', 0))"))


def admin_holders_after(
    users: list[tuple[uuid.UUID, list[str]]],
    role_perms: Mapping[str, frozenset[str]],
) -> set[uuid.UUID]:
    """Пользователи (из переданных активных), у которых есть `admin:roles` через роли."""
    admin_roles = {code for code, perms in role_perms.items() if ADMIN_ROLES_PERMISSION in perms}
    return {user_id for user_id, roles in users if admin_roles & set(roles or ())}


def validate_permissions(codes: Iterable[str]) -> list[str]:
    """Коды прав по каталогу и зависимостям; 400 `validation_error` с полем `permissions`."""
    selected = list(dict.fromkeys(codes))
    unknown = [code for code in selected if code not in PERMISSION_CODES]
    if unknown:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "permissions",
                    "code": "unknown_permission",
                    "message": "Неизвестные права: " + ", ".join(unknown),
                }
            ],
        )
    missing = missing_requirements(selected)
    if missing:
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "permissions",
                    "code": "permission_dependency",
                    "message": (
                        f"Право «{permission_name(code)}» требует: "
                        + ", ".join(f"«{permission_name(req)}»" for req in requires)
                    ),
                    "permission": code,
                    "requires": list(requires),
                }
                for code, requires in missing.items()
            ],
        )
    return _sorted_permissions(selected)


# --------------------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------------------


async def _role_permissions(
    session: AsyncSession, role_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, frozenset[str]]:
    stmt = select(AppRolePermission.role_id, AppRolePermission.permission_code)
    if role_ids is not None:
        stmt = stmt.where(AppRolePermission.role_id.in_(list(role_ids)))
    result: dict[uuid.UUID, set[str]] = {}
    for role_id, code in (await session.execute(stmt)).all():
        result.setdefault(role_id, set()).add(code)
    return {role_id: frozenset(codes) for role_id, codes in result.items()}


async def _users_count_by_role(session: AsyncSession) -> dict[str, int]:
    code = func.unnest(AppUser.roles).label("code")
    subquery = select(code).subquery()
    rows = await session.execute(select(subquery.c.code, func.count()).group_by(subquery.c.code))
    return {role_code: int(count) for role_code, count in rows.all()}


def serialize_role(
    role: AppRole, permissions: Iterable[str], users_count: int = 0
) -> dict[str, Any]:
    perms = _sorted_permissions(code for code in permissions if code in PERMISSION_CODES)
    return {
        "id": str(role.id),
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "is_locked": role.is_locked,
        "workflow_types": _sorted_types(role.workflow_types or ()),
        "record_scope": role.record_scope,
        "permissions": perms,
        "permissions_count": len(perms),
        "users_count": users_count,
        "created_at": role.created_at,
        "updated_at": role.updated_at,
        "version": role.xmin,
    }


def _role_sort_key(role: AppRole) -> tuple[int, int, str]:
    if role.code in SYSTEM_ROLE_CODES:
        return (0, SYSTEM_ROLE_CODES.index(role.code), "")
    return (1, 0, role.name.lower())


async def list_roles(session: AsyncSession) -> list[dict[str, Any]]:
    roles = list((await session.execute(select(AppRole))).scalars().all())
    roles.sort(key=_role_sort_key)
    perms = await _role_permissions(session)
    counts = await _users_count_by_role(session)
    return [
        serialize_role(role, perms.get(role.id, frozenset()), counts.get(role.code, 0))
        for role in roles
    ]


async def transitions_coverage(session: AsyncSession, role_code: str) -> dict[str, dict[str, int]]:
    """По процессам: переходы с ограничением по ролям и сколько из них разрешены роли.

    `{"b2b": {"restricted": 14, "allowed": 12}}` — для предупреждения в редакторе роли:
    пользователи роли не смогут двигать заявки по остальным ограниченным переходам.
    """
    restricted = func.count().filter(func.jsonb_array_length(WorkflowTransition.allowed_roles) > 0)
    allowed = func.count().filter(WorkflowTransition.allowed_roles.contains([role_code]))
    rows = await session.execute(
        select(Workflow.code, restricted, allowed)
        .join(Workflow, Workflow.id == WorkflowTransition.workflow_id)
        .where(WorkflowTransition.deleted_at.is_(None))
        .group_by(Workflow.code)
    )
    result = {wf: {"restricted": 0, "allowed": 0} for wf in WORKFLOW_TYPES}
    for wf, restricted_count, allowed_count in rows.all():
        result[wf] = {"restricted": int(restricted_count), "allowed": int(allowed_count)}
    return result


async def _transitions_with_role(session: AsyncSession, role_code: str) -> list[str]:
    rows = await session.execute(
        select(Workflow.code, WorkflowTransition.name)
        .join(Workflow, Workflow.id == WorkflowTransition.workflow_id)
        .where(
            WorkflowTransition.deleted_at.is_(None),
            WorkflowTransition.allowed_roles.contains([role_code]),
        )
        .order_by(Workflow.code, WorkflowTransition.sort_order)
    )
    return [
        f"«{name or 'Без названия'}» ({WORKFLOW_TYPE_LABELS.get(wf, wf)})"
        for wf, name in rows.all()
    ]


def _differs_from_default(role: AppRole, permissions: frozenset[str]) -> bool | None:
    definition = SYSTEM_ROLES.get(role.code)
    if not role.is_system or definition is None:
        return None
    return (
        permissions != definition.permissions
        or set(role.workflow_types or ()) != set(WORKFLOW_TYPES)
        or role.record_scope != "all"
    )


async def _load_role(session: AsyncSession, role_id: uuid.UUID, *, lock: bool = False) -> AppRole:
    stmt = select(AppRole).where(AppRole.id == role_id)
    if lock:
        stmt = stmt.with_for_update()
    role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        raise not_found("Роль не найдена")
    return role


async def get_role(session: AsyncSession, role_id: uuid.UUID) -> dict[str, Any]:
    role = await _load_role(session, role_id)
    perms = (await _role_permissions(session, [role.id])).get(role.id, frozenset())
    users = (
        (
            await session.execute(
                select(AppUser)
                .where(AppUser.roles.any(role.code))
                .order_by(AppUser.full_name, AppUser.username)
            )
        )
        .scalars()
        .all()
    )
    payload = serialize_role(role, perms, len(users))
    payload["users"] = [
        {
            "id": str(user.id),
            "full_name": user.full_name,
            "username": user.username,
            "is_active": user.is_active,
        }
        for user in users
    ]
    payload["coverage"] = await transitions_coverage(session, role.code)
    payload["differs_from_default"] = _differs_from_default(role, perms)
    return payload


def permission_groups() -> list[dict[str, Any]]:
    """Каталог прав для редактора ролей: группы в порядке UI, права с зависимостями."""
    groups: list[dict[str, Any]] = []
    for group_code, group_name in PERMISSION_GROUPS:
        groups.append(
            {
                "code": group_code,
                "name": group_name,
                "permissions": [
                    {
                        "code": definition.code,
                        "name": definition.name,
                        "description": definition.description,
                        "scoped": definition.scoped,
                        "requires": list(definition.requires),
                    }
                    for definition in PERMISSIONS
                    if definition.group == group_code
                ],
            }
        )
    return groups


# --------------------------------------------------------------------------------------
# Защита от потери управления
# --------------------------------------------------------------------------------------


async def _current_role_perms(session: AsyncSession) -> dict[str, frozenset[str]]:
    roles = (await session.execute(select(AppRole.id, AppRole.code))).all()
    perms = await _role_permissions(session)
    return {code: perms.get(role_id, frozenset()) for role_id, code in roles}


async def ensure_no_lockout(
    session: AsyncSession,
    *,
    actor: AppUser | None,
    user_roles_override: Mapping[uuid.UUID, list[str]] | None = None,
    role_perms_override: Mapping[str, frozenset[str]] | None = None,
) -> None:
    """409 `access_lockout`, если после изменения актор теряет `admin:roles` или не
    остаётся ни одного активного пользователя с этим правом. Вызывать под RBAC-локом."""
    role_perms = await _current_role_perms(session)
    rows = (
        await session.execute(select(AppUser.id, AppUser.roles).where(AppUser.is_active.is_(True)))
    ).all()
    users_before = [(user_id, list(roles or ())) for user_id, roles in rows]
    holders_before = admin_holders_after(users_before, role_perms)

    overrides = dict(user_roles_override or {})
    users_after = [(user_id, overrides.get(user_id, roles)) for user_id, roles in users_before]
    perms_after = {**role_perms, **dict(role_perms_override or {})}
    holders_after = admin_holders_after(users_after, perms_after)

    if actor is not None and actor.id in holders_before and actor.id not in holders_after:
        raise _conflict("access_lockout", SELF_LOCKOUT_MESSAGE)
    if not holders_after:
        raise _conflict("access_lockout", LAST_ADMIN_MESSAGE)


async def after_rbac_change(
    *, role_codes: Iterable[str] = (), user_ids: Iterable[uuid.UUID] = ()
) -> None:
    """После commit: локальный сброс кэша каталога + SSE `access.changed` всем репликам и
    открытым интерфейсам (в событии — только коды ролей и id пользователей)."""
    reset_role_catalog()
    await publish_event(
        SSE_ACCESS_CHANGED,
        {
            "role_codes": sorted(set(role_codes)),
            "user_ids": sorted({str(user_id) for user_id in user_ids}),
        },
    )


# --------------------------------------------------------------------------------------
# Мутации ролей
# --------------------------------------------------------------------------------------


async def _ensure_unique(
    session: AsyncSession, *, code: str | None, name: str | None, exclude_id: uuid.UUID | None
) -> None:
    if code is not None:
        exists = (
            await session.execute(select(AppRole.id).where(AppRole.code == code))
        ).scalar_one_or_none()
        if exists is not None:
            raise _conflict(
                "role_exists",
                f"Роль с кодом «{code}» уже существует",
                [{"field": "code", "code": "role_exists", "message": "Код уже занят"}],
            )
    if name is not None:
        stmt = select(AppRole.id).where(func.lower(AppRole.name) == name.lower())
        if exclude_id is not None:
            stmt = stmt.where(AppRole.id != exclude_id)
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            raise _conflict(
                "role_exists",
                f"Роль с названием «{name}» уже существует",
                [{"field": "name", "code": "role_exists", "message": "Название уже занято"}],
            )


async def _replace_permissions(
    session: AsyncSession, role_id: uuid.UUID, permissions: Iterable[str]
) -> None:
    await session.execute(delete(AppRolePermission).where(AppRolePermission.role_id == role_id))
    session.add_all(
        AppRolePermission(role_id=role_id, permission_code=code) for code in sorted(permissions)
    )
    await session.flush()


async def create_role(
    session: AsyncSession, *, actor: AppUser, body: Any, ip: str | None
) -> dict[str, Any]:
    await acquire_rbac_lock(session)
    permissions = validate_permissions(body.permissions)
    await _ensure_unique(session, code=body.code, name=body.name, exclude_id=None)
    role = AppRole(
        code=body.code,
        name=body.name,
        description=body.description,
        is_system=False,
        is_locked=False,
        workflow_types=_sorted_types(body.workflow_types),
        record_scope=body.record_scope,
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(role)
    await session.flush()
    await _replace_permissions(session, role.id, permissions)
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="role.created",
        entity_type="app_role",
        entity_id=role.id,
        after={
            "code": role.code,
            "name": role.name,
            "permissions": permissions,
            "workflow_types": role.workflow_types,
            "record_scope": role.record_scope,
        },
        ip=ip,
    )
    await session.commit()
    await session.refresh(role)
    await after_rbac_change(role_codes=[role.code])
    return serialize_role(role, permissions, 0)


async def update_role(
    session: AsyncSession, *, actor: AppUser, role_id: uuid.UUID, body: Any, ip: str | None
) -> dict[str, Any]:
    await acquire_rbac_lock(session)
    role = await _load_role(session, role_id, lock=True)
    check_version(role, body.version)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    current = (await _role_permissions(session, [role.id])).get(role.id, frozenset())

    new_permissions = (
        frozenset(validate_permissions(changes["permissions"]))
        if changes.get("permissions") is not None
        else current
    )
    new_types = (
        _sorted_types(changes["workflow_types"])
        if changes.get("workflow_types") is not None
        else _sorted_types(role.workflow_types or ())
    )
    new_scope = changes.get("record_scope") or role.record_scope
    scope_changed = (
        new_permissions != current
        or new_types != _sorted_types(role.workflow_types or ())
        or new_scope != role.record_scope
    )
    if role.is_locked and scope_changed:
        raise _conflict(
            "role_locked",
            "Права и область администратора не редактируются — это защищает систему "
            "от потери управления",
        )
    new_name = changes.get("name")
    if new_name is not None and new_name != role.name:
        await _ensure_unique(session, code=None, name=new_name, exclude_id=role.id)
    if new_permissions != current:
        await ensure_no_lockout(
            session, actor=actor, role_perms_override={role.code: new_permissions}
        )

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if new_name is not None and new_name != role.name:
        before["name"], after["name"] = role.name, new_name
        role.name = new_name
    if "description" in changes and changes["description"] != role.description:
        before["description"], after["description"] = role.description, changes["description"]
        role.description = changes["description"]
    if new_types != _sorted_types(role.workflow_types or ()):
        before["workflow_types"], after["workflow_types"] = list(role.workflow_types), new_types
        role.workflow_types = new_types
    if new_scope != role.record_scope:
        before["record_scope"], after["record_scope"] = role.record_scope, new_scope
        role.record_scope = new_scope
    if new_permissions != current:
        after["permissions_added"] = _sorted_permissions(new_permissions - current)
        after["permissions_removed"] = _sorted_permissions(current - new_permissions)
        await _replace_permissions(session, role.id, new_permissions)
    if before or after:
        role.updated_by = actor.id
        role.updated_at = func.now()
        await write_audit(
            session,
            actor_user_id=actor.id,
            action="role.updated",
            entity_type="app_role",
            entity_id=role.id,
            before=before or None,
            after=after,
            ip=ip,
        )
    await session.commit()
    await session.refresh(role)
    if before or after:
        await after_rbac_change(role_codes=[role.code])
    counts = await _users_count_by_role(session)
    return serialize_role(role, new_permissions, counts.get(role.code, 0))


async def reset_system_role(
    session: AsyncSession, *, actor: AppUser, role_id: uuid.UUID, version: int, ip: str | None
) -> dict[str, Any]:
    """Сброс системной роли к набору прав и области по умолчанию из кода."""
    await acquire_rbac_lock(session)
    role = await _load_role(session, role_id, lock=True)
    definition = SYSTEM_ROLES.get(role.code)
    if not role.is_system or definition is None:
        raise validation_error("Сбросить к стандартным правам можно только системную роль")
    if role.is_locked:
        raise _conflict(
            "role_locked",
            "Права и область администратора не редактируются — это защищает систему "
            "от потери управления",
        )
    check_version(role, version)
    current = (await _role_permissions(session, [role.id])).get(role.id, frozenset())
    await ensure_no_lockout(
        session, actor=actor, role_perms_override={role.code: definition.permissions}
    )
    before = {
        "permissions": _sorted_permissions(current),
        "workflow_types": _sorted_types(role.workflow_types or ()),
        "record_scope": role.record_scope,
    }
    await _replace_permissions(session, role.id, definition.permissions)
    role.workflow_types = list(WORKFLOW_TYPES)
    role.record_scope = "all"
    role.updated_by = actor.id
    role.updated_at = func.now()
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="role.reset",
        entity_type="app_role",
        entity_id=role.id,
        before=before,
        after={
            "permissions_added": _sorted_permissions(definition.permissions - current),
            "permissions_removed": _sorted_permissions(current - definition.permissions),
        },
        ip=ip,
    )
    await session.commit()
    await session.refresh(role)
    await after_rbac_change(role_codes=[role.code])
    counts = await _users_count_by_role(session)
    return serialize_role(role, definition.permissions, counts.get(role.code, 0))


ROLE_DELETE_BUSY_MESSAGE = (
    "Схема процесса сейчас обновляется — роль не удалена, повторите через несколько секунд"
)


async def lock_workflows_for_role_change(session: AsyncSession) -> None:
    """Эксклюзивные локи всех воронок в фиксированном порядке b2b → b2c (после RBAC-лока):
    пока роль удаляется, применение пакетов схемы ждёт и не добавит её код в переходы.
    Таймаут ожидания — 409 `workflow_busy` (`lock_timeout_as_busy`)."""
    workflow_ids = dict((await session.execute(select(Workflow.code, Workflow.id))).all())
    async with lock_timeout_as_busy(ROLE_DELETE_BUSY_MESSAGE):
        for wf in WORKFLOW_TYPES:
            if wf in workflow_ids:
                await acquire_exclusive_workflow_lock(session, workflow_ids[wf])


async def delete_role(
    session: AsyncSession, *, actor: AppUser, role_id: uuid.UUID, ip: str | None
) -> None:
    await acquire_rbac_lock(session)
    await lock_workflows_for_role_change(session)

    role = await _load_role(session, role_id, lock=True)
    if role.is_system:
        raise _conflict("role_system", "Системную роль удалить нельзя")
    users_count = (
        await session.execute(
            select(func.count()).select_from(AppUser).where(AppUser.roles.any(role.code))
        )
    ).scalar_one()
    transitions = await _transitions_with_role(session, role.code)
    if users_count or transitions:
        details: list[dict[str, Any]] = []
        if users_count:
            details.append(
                {
                    "field": "users",
                    "code": "role_in_use",
                    "message": f"Роль назначена пользователям: {users_count}",
                    "count": int(users_count),
                }
            )
        if transitions:
            details.append(
                {
                    "field": "transitions",
                    "code": "role_in_use",
                    "message": "Роль указана в переходах: " + ", ".join(transitions),
                    "items": transitions,
                }
            )
        raise _conflict(
            "role_in_use",
            "Роль используется — снимите её с пользователей и уберите из переходов процессов",
            details,
        )
    permissions = (await _role_permissions(session, [role.id])).get(role.id, frozenset())
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="role.deleted",
        entity_type="app_role",
        entity_id=role.id,
        before={
            "code": role.code,
            "name": role.name,
            "permissions": _sorted_permissions(permissions),
            "workflow_types": _sorted_types(role.workflow_types or ()),
            "record_scope": role.record_scope,
        },
        ip=ip,
    )
    code = role.code
    await session.delete(role)
    await session.commit()
    await after_rbac_change(role_codes=[code])


# --------------------------------------------------------------------------------------
# Назначение ролей пользователю
# --------------------------------------------------------------------------------------


async def set_user_roles(
    session: AsyncSession,
    *,
    actor: AppUser | None,
    user_id: uuid.UUID,
    roles: list[str],
    version: int | None,
    ip: str | None,
    source: str | None = None,
) -> AppUser:
    """Назначает пользователю роли (полная замена списка); возвращает пользователя."""
    await acquire_rbac_lock(session)
    user = (
        await session.execute(select(AppUser).where(AppUser.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        raise not_found("Пользователь не найден")
    if version is not None:
        check_version(user, version)
    catalog = await ensure_role_catalog(session, force=True)
    duplicates = sorted({code for code in roles if roles.count(code) > 1})
    unknown = [code for code in dict.fromkeys(roles) if code not in catalog.roles]
    if duplicates or unknown:
        message = (
            "Неизвестные роли: " + ", ".join(unknown)
            if unknown
            else "Роли повторяются: " + ", ".join(duplicates)
        )
        raise validation_error(
            "Ошибка валидации данных",
            [
                {
                    "field": "roles",
                    "code": "unknown_role" if unknown else "duplicate_role",
                    "message": message,
                }
            ],
        )
    new_roles = sorted(roles)
    old_roles = sorted(user.roles or [])
    if new_roles != old_roles:
        await ensure_no_lockout(session, actor=actor, user_roles_override={user.id: new_roles})
        user.roles = new_roles
        after: dict[str, Any] = {"roles": new_roles}
        if source:
            after["source"] = source
        await write_audit(
            session,
            actor_user_id=actor.id if actor is not None else None,
            action="user.roles_changed",
            entity_type="app_user",
            entity_id=user.id,
            before={"roles": old_roles},
            after=after,
            ip=ip,
        )
    await session.commit()
    await session.refresh(user)
    if new_roles != old_roles:
        await after_rbac_change(role_codes=set(old_roles) ^ set(new_roles), user_ids=[user.id])
    return user
