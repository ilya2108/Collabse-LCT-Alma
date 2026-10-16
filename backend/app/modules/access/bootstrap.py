"""Идемпотентная синхронизация каталога прав и системных ролей из кода в БД.

Миграция 0004 засевает свою литеральную копию каталога; эта функция приводит БД к коду
после её изменений (новые права следующих версий), её вызывают `tools/seed.py`,
аварийная команда `tools/rbac.py` и тесты с БД. Правила:
- `app_permission` — upsert всех прав кода; права, которых в коде больше нет, удаляются
  (каскадом из ролей): право без проверки в коде ничего не значит;
- системная роль, которой нет в БД, создаётся с набором по умолчанию; у существующих
  системных ролей наборы, настроенные администратором, не трогаются — кроме
  заблокированной admin: у неё всегда полный набор из кода (защита от потери управления).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac_catalog import PERMISSION_CODES, PERMISSIONS, SYSTEM_ROLES, WORKFLOW_TYPES
from app.models.access import AppPermission, AppRole, AppRolePermission


async def seed_rbac_catalog(session: AsyncSession) -> None:
    """Upsert прав и системных ролей из кода (flush, без commit)."""
    rows = [
        {
            "code": definition.code,
            "group_code": definition.group,
            "name": definition.name,
            "description": definition.description,
            "is_scoped": definition.scoped,
            "sort_order": index * 10,
        }
        for index, definition in enumerate(PERMISSIONS, start=1)
    ]
    stmt = insert(AppPermission).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[AppPermission.code],
            set_={
                "group_code": stmt.excluded.group_code,
                "name": stmt.excluded.name,
                "description": stmt.excluded.description,
                "is_scoped": stmt.excluded.is_scoped,
                "sort_order": stmt.excluded.sort_order,
            },
        )
    )
    await session.execute(
        delete(AppPermission).where(AppPermission.code.not_in(sorted(PERMISSION_CODES)))
    )

    existing = {
        role.code: role
        for role in (
            await session.execute(select(AppRole).where(AppRole.code.in_(list(SYSTEM_ROLES))))
        ).scalars()
    }
    for code, definition in SYSTEM_ROLES.items():
        role = existing.get(code)
        if role is None:
            role = AppRole(
                code=code,
                name=definition.name,
                description=definition.description,
                is_system=True,
                is_locked=definition.locked,
                workflow_types=list(WORKFLOW_TYPES),
                record_scope="all",
            )
            session.add(role)
            await session.flush()
            session.add_all(
                AppRolePermission(role_id=role.id, permission_code=permission)
                for permission in sorted(definition.permissions)
            )
            continue
        if not role.is_system or role.is_locked != definition.locked:
            role.is_system = True
            role.is_locked = definition.locked
        if definition.locked:
            current = set(
                (
                    await session.execute(
                        select(AppRolePermission.permission_code).where(
                            AppRolePermission.role_id == role.id
                        )
                    )
                ).scalars()
            )
            missing = definition.permissions - current
            extra = current - definition.permissions
            if extra:
                await session.execute(
                    delete(AppRolePermission).where(
                        AppRolePermission.role_id == role.id,
                        AppRolePermission.permission_code.in_(sorted(extra)),
                    )
                )
            session.add_all(
                AppRolePermission(role_id=role.id, permission_code=permission)
                for permission in sorted(missing)
            )
            if sorted(role.workflow_types or []) != sorted(WORKFLOW_TYPES):
                role.workflow_types = list(WORKFLOW_TYPES)
            if role.record_scope != "all":
                role.record_scope = "all"
    await session.flush()
