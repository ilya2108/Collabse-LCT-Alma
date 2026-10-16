"""Совместимость: наборы прав системных ролей по умолчанию.

Каталог прав, наборы системных ролей и зависимости — `app/core/rbac_catalog.py`;
эффективные права пользователя (свои роли из БД, область B2B/B2C) — `app/core/access.py`
(`user.access`), их отдаёт `GET /auth/me`. Модуль оставлен для старых импортов и тестов:
считает права только по статическому каталогу системных ролей.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.core.rbac_catalog import SYSTEM_ROLES

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    code: definition.permissions for code, definition in SYSTEM_ROLES.items()
}


def permissions_for_roles(roles: Iterable[str]) -> list[str]:
    """Объединение прав системных ролей, отсортированный список (свои роли не учитываются)."""
    result: set[str] = set()
    for role in roles:
        result |= ROLE_PERMISSIONS.get(role, frozenset())
    return sorted(result)
