"""Единственный источник истины по вычислению `permissions` для `GET /auth/me`.

RBAC-матрица — api-contract.md §12 (фронт скрывает разделы по этим строкам,
backend всё равно дублирует проверки ролей на каждом эндпоинте).
"""

from __future__ import annotations

from collections.abc import Iterable

# --- базовые наборы --------------------------------------------------------------------

_OBSERVER: frozenset[str] = frozenset(
    {
        "requests:read",
        "universities:read",
        "contracts:read",
        "products:read",
        "programs:read",
        "students:read",  # все выдачи маскированы; раскрытие observer'у недоступно
        "activities:read",
        "reports:read",
        "export:run",  # экспорт с маскированными ПДн
        "workflow:read",
        "notifications:self",
        "admin:audit",  # §12.1: аудит и журнал интеграций observer читает
    }
)

_KAM: frozenset[str] = _OBSERVER - {"admin:audit"} | frozenset(
    {
        "requests:write",
        "requests:transition",
        "requests:assign",  # взять неназначенную на себя
        "comments:write",
        "universities:write",
        "contracts:write",
        "programs:write",
        "programs:priority",
        "students:write",
        "students:reveal",
        "students:files",
        "import:run",
    }
)

_HEAD_KAM: frozenset[str] = _KAM | frozenset(
    {
        "products:write",
        "contracts:delete",
        "workflow:change",  # пакеты изменений и impact-preview
        "activities:write",
        "admin:users",  # список пользователей — read
    }
)

_ADMIN: frozenset[str] = _HEAD_KAM | frozenset(
    {
        "universities:delete",
        "products:delete",
        "programs:delete",
        "requests:delete",
        "requests:reopen",
        "students:delete",
        "workflow:approve",
        "import:dictionaries",
        "admin:users_write",
        "admin:settings",
        "admin:dictionaries",
        "admin:flags",
        "admin:audit",
        "admin:integrations",
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": _ADMIN,
    "head_kam": _HEAD_KAM,
    "kam": _KAM,
    "observer": _OBSERVER,
}


def permissions_for_roles(roles: Iterable[str]) -> list[str]:
    """Объединение permissions по всем ролям пользователя, отсортированный список."""
    result: set[str] = set()
    for role in roles:
        result |= ROLE_PERMISSIONS.get(role, frozenset())
    return sorted(result)
