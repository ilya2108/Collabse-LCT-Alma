"""Фабрики ролей F2 для тестов с БД (общие фабрики — tests/db/factories.py)."""

from __future__ import annotations

import itertools
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac_catalog import SYSTEM_ROLES, WORKFLOW_TYPES
from app.models.access import AppRole, AppRolePermission

_seq = itertools.count(1)

KAM_PERMISSIONS = SYSTEM_ROLES["kam"].permissions


async def make_role(
    session: AsyncSession,
    *,
    code: str | None = None,
    permissions: Iterable[str] = KAM_PERMISSIONS,
    workflow_types: Iterable[str] = WORKFLOW_TYPES,
    record_scope: str = "all",
    name: str | None = None,
    description: str | None = None,
) -> AppRole:
    """Своя роль с набором прав и областью (flush, без commit)."""
    n = next(_seq)
    code = code or f"test_role_{n}"
    role = AppRole(
        code=code,
        name=name or f"Тестовая роль {code}",
        description=description,
        is_system=False,
        is_locked=False,
        workflow_types=[wf for wf in WORKFLOW_TYPES if wf in set(workflow_types)],
        record_scope=record_scope,
    )
    session.add(role)
    await session.flush()
    session.add_all(
        AppRolePermission(role_id=role.id, permission_code=permission)
        for permission in sorted(set(permissions))
    )
    await session.flush()
    return role
