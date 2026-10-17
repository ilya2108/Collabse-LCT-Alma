"""Эффективные права пользователя: каталог ролей, объединение прав, область B2B/B2C.

Модель (Р-F2-2…Р-F2-4, Р-F2-8):
- назначения — массив `app_user.roles` (коды ролей); роли и их права — таблицы
  `app_role`/`app_role_permission`, права определяет код (`core/rbac_catalog.py`);
- каталог ролей (≈10 строк) живёт в памяти процесса: TTL `RBAC_CATALOG_TTL_SECONDS`
  (5 с), загрузка одним SELECT под `asyncio.Lock`; после изменения RBAC сервис сбрасывает
  кэш локально и публикует SSE `access.changed` — хук ниже сбрасывает кэш на каждой
  реплике (pub/sub `crm:v1:events`), при недоступном KeyDB страхует TTL;
- права нескольких ролей объединяются ПО ПРОЦЕССУ: в процессе P действуют права с областью
  тех ролей, у которых P входит в `workflow_types`; записи процесса — `all`, если хоть одна
  такая роль даёт `all`, иначе `own`. Общие права (справочники, админка) — объединение всех;
- без загруженного каталога (юнит-тесты, холодный старт) действует статический каталог
  системных ролей из кода — поведение 4 базовых ролей от этого не зависит.

Ядро чистое (кроме загрузки каталога): `compute_access` и `DealScope` тестируются без БД.
"""

from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.core.events import SSE_ACCESS_CHANGED
from app.core.rbac_catalog import (
    PERMISSION_CODES,
    SCOPED_PERMISSIONS,
    SYSTEM_ROLES,
    WORKFLOW_TYPES,
)
from app.core.sse import register_local_hook

RecordScope = Literal["all", "own"]

_ACCESS_CACHE_SIZE = 256


# --------------------------------------------------------------------------------------
# Каталог ролей
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleGrant:
    """Роль каталога: набор прав и область данных."""

    code: str
    name: str
    is_system: bool
    permissions: frozenset[str]
    workflow_types: frozenset[str]
    record_scope: RecordScope
    description: str | None = None
    is_locked: bool = False


@dataclass(frozen=True)
class RoleCatalog:
    roles: Mapping[str, RoleGrant]
    version: int
    loaded_at: float

    def role_codes_with(self, permission: str, workflow_type: str | None = None) -> tuple[str, ...]:
        """Коды ролей с правом; для права с областью и заданным процессом — только роли,
        действующие в этом процессе."""
        scoped = permission in SCOPED_PERMISSIONS
        return tuple(
            code
            for code, role in sorted(self.roles.items())
            if permission in role.permissions
            and (not scoped or workflow_type is None or workflow_type in role.workflow_types)
        )

    def names(self, codes: Iterable[str]) -> list[str]:
        """Названия ролей по кодам (неизвестные коды пропускаются)."""
        return [self.roles[code].name for code in codes if code in self.roles]

    def transition_workflow_types(self, code: str) -> tuple[str, ...]:
        """Процессы, в которых роль может выполнять переходы (`requests:transition`)."""
        role = self.roles.get(code)
        if role is None or "requests:transition" not in role.permissions:
            return ()
        return tuple(wf for wf in WORKFLOW_TYPES if wf in role.workflow_types)


_static_catalog: RoleCatalog | None = None
_snapshot: RoleCatalog | None = None
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None
_versions = itertools.count(1)
# поколение сброса: загрузка, начатая до reset_role_catalog, не кладёт в кэш устаревший снимок
_generation = 0
_access_cache: OrderedDict[tuple[int, tuple[str, ...]], EffectiveAccess] = OrderedDict()


def static_catalog() -> RoleCatalog:
    """Каталог системных ролей из кода (юнит-тесты, холодный старт, запасной вариант)."""
    global _static_catalog
    if _static_catalog is None:
        _static_catalog = RoleCatalog(
            roles={
                code: RoleGrant(
                    code=code,
                    name=definition.name,
                    is_system=True,
                    permissions=definition.permissions,
                    workflow_types=frozenset(WORKFLOW_TYPES),
                    record_scope="all",
                    description=definition.description,
                    is_locked=definition.locked,
                )
                for code, definition in SYSTEM_ROLES.items()
            },
            version=0,
            loaded_at=0.0,
        )
    return _static_catalog


def get_catalog_snapshot() -> RoleCatalog:
    """Последний загруженный каталог (без запроса в БД) или статический."""
    return _snapshot if _snapshot is not None else static_catalog()


def reset_role_catalog() -> None:
    """Локальная инвалидация: следующий `ensure_role_catalog` перечитает БД.

    Вызывается сервисом ролей после commit и хуком SSE `access.changed` на каждой реплике;
    тесты вызывают её до и после каждого теста (кэш не переживает откат транзакции).
    """
    global _snapshot, _lock, _lock_loop, _generation
    _generation += 1
    _snapshot = None
    _lock = None
    _lock_loop = None
    _access_cache.clear()


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


def _is_fresh(catalog: RoleCatalog | None) -> bool:
    if catalog is None:
        return False
    return time.monotonic() - catalog.loaded_at < get_settings().rbac_catalog_ttl_seconds


async def load_role_catalog(session: AsyncSession) -> RoleCatalog:
    """Читает каталог из БД одним SELECT (роли + агрегированные коды прав)."""
    from app.models.access import AppRole, AppRolePermission

    permissions_agg = func.coalesce(
        func.array_agg(AppRolePermission.permission_code).filter(
            AppRolePermission.permission_code.is_not(None)
        ),
        sa.literal_column("'{}'::text[]"),
    )
    rows = (
        await session.execute(
            select(
                AppRole.code,
                AppRole.name,
                AppRole.description,
                AppRole.is_system,
                AppRole.is_locked,
                AppRole.workflow_types,
                AppRole.record_scope,
                permissions_agg,
            )
            .outerjoin(AppRolePermission, AppRolePermission.role_id == AppRole.id)
            .group_by(AppRole.id)
        )
    ).all()
    roles: dict[str, RoleGrant] = {}
    for code, name, description, is_system, is_locked, wf_types, scope, perms in rows:
        roles[code] = RoleGrant(
            code=code,
            name=name,
            is_system=is_system,
            # права, которых больше нет в коде, не действуют (каталог определяет код)
            permissions=frozenset(perms or ()) & PERMISSION_CODES,
            workflow_types=frozenset(wf_types or ()) & frozenset(WORKFLOW_TYPES),
            record_scope="own" if scope == "own" else "all",
            description=description,
            is_locked=is_locked,
        )
    return RoleCatalog(roles=roles, version=next(_versions), loaded_at=time.monotonic())


async def ensure_role_catalog(session: AsyncSession, *, force: bool = False) -> RoleCatalog:
    """Актуальный каталог: из памяти процесса (TTL) или загрузка из БД.

    Загрузчик один на процесс (`asyncio.Lock`): параллельные запросы после сброса кэша
    ждут одну загрузку, а не идут в БД каждый.
    """
    global _snapshot
    if not force and _is_fresh(_snapshot):
        return _snapshot  # type: ignore[return-value]
    async with _get_lock():
        if not force and _is_fresh(_snapshot):
            return _snapshot  # type: ignore[return-value]
        generation = _generation
        catalog = await load_role_catalog(session)
        if generation == _generation:
            _snapshot = catalog
            _access_cache.clear()
        return catalog


# --------------------------------------------------------------------------------------
# Область заявок
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DealScope:
    """Область заявок: правила `(процесс, id ответственного | None)`.

    `None` — все заявки процесса, id — только заявки, где пользователь ответственный.
    Процессов нет в правилах — заявки процесса недоступны.
    """

    rules: tuple[tuple[str, uuid.UUID | None], ...]

    @classmethod
    def unrestricted(cls) -> DealScope:
        """Все заявки всех процессов (служебные выборки, совместимость с `scope=None`)."""
        return cls(rules=tuple((wf, None) for wf in WORKFLOW_TYPES))

    @property
    def workflow_types(self) -> tuple[str, ...]:
        return tuple(wf for wf, _owner in self.rules)

    @property
    def own_user_id(self) -> uuid.UUID | None:
        """id пользователя, если во всех процессах области — только свои заявки."""
        owners = {owner for _wf, owner in self.rules}
        if len(owners) == 1:
            (owner,) = owners
            return owner
        return None

    @property
    def is_empty(self) -> bool:
        return not self.rules

    @property
    def is_unrestricted(self) -> bool:
        return set(self.workflow_types) >= set(WORKFLOW_TYPES) and all(
            owner is None for _wf, owner in self.rules
        )

    def owner_for(self, pipeline: str) -> tuple[bool, uuid.UUID | None]:
        """(процесс в области, ограничение по ответственному)."""
        for wf, owner in self.rules:
            if wf == pipeline:
                return True, owner
        return False, None

    def clause(
        self,
        pipeline_col: Any = None,
        responsible_col: Any = None,
    ) -> ColumnElement[bool]:
        """SQL-условие области: `true` | OR(pipeline = wf [AND responsible = uid]) | `false`."""
        if pipeline_col is None or responsible_col is None:
            from app.models.deal import Deal

            pipeline_col = Deal.pipeline if pipeline_col is None else pipeline_col
            responsible_col = (
                Deal.responsible_user_id if responsible_col is None else responsible_col
            )
        if not self.rules:
            return sa.false()
        if set(self.workflow_types) >= set(WORKFLOW_TYPES):
            owners = {owner for _wf, owner in self.rules}
            if owners == {None}:
                return sa.true()
            if len(owners) == 1:
                (owner,) = owners
                return responsible_col == owner
        parts: list[ColumnElement[bool]] = []
        for wf, owner in self.rules:
            condition: ColumnElement[bool] = pipeline_col == wf
            if owner is not None:
                condition = sa.and_(condition, responsible_col == owner)
            parts.append(condition)
        return parts[0] if len(parts) == 1 else sa.or_(*parts)

    def workflow_clause(self, pipeline_col: Any = None) -> ColumnElement[bool]:
        """Только ограничение по процессам (агрегаты справочников, где «свои» не применимы)."""
        if pipeline_col is None:
            from app.models.deal import Deal

            pipeline_col = Deal.pipeline
        if not self.rules:
            return sa.false()
        if set(self.workflow_types) >= set(WORKFLOW_TYPES):
            return sa.true()
        return pipeline_col.in_(self.workflow_types)

    def apply(self, stmt: sa.Select) -> sa.Select:
        """`stmt.where(clause)`; при полном доступе выборка не меняется."""
        if self.is_unrestricted:
            return stmt
        return stmt.where(self.clause())

    def allows(self, pipeline: str, responsible_user_id: uuid.UUID | None) -> bool:
        in_scope, owner = self.owner_for(pipeline)
        return in_scope and (owner is None or owner == responsible_user_id)

    def cache_key(self) -> str:
        """Ключ области для кэша отчётов: `b2b:*|b2c:<uuid>`; пустая область — `-`."""
        if not self.rules:
            return "-"
        return "|".join(f"{wf}:{owner or '*'}" for wf, owner in self.rules)


# --------------------------------------------------------------------------------------
# Эффективные права
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveAccess:
    role_codes: tuple[str, ...]  # только известные каталогу
    permissions: frozenset[str]  # объединение всех прав (для /auth/me и общих прав)
    by_workflow: Mapping[str, frozenset[str]]  # процесс → объединение прав с областью
    roles_by_workflow: Mapping[str, frozenset[str]]  # процесс → роли, действующие в нём
    records: Mapping[str, RecordScope]  # процесс → все / только свои
    general: frozenset[str] = frozenset()  # общие права (без области)
    transition_roles_by_workflow: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def has(self, permission: str, workflow_type: str | None = None) -> bool:
        """Право с областью: без процесса — «хоть в одном процессе», с процессом — в нём.
        Общее право: процесс игнорируется."""
        if permission in SCOPED_PERMISSIONS:
            if workflow_type is None:
                return any(permission in perms for perms in self.by_workflow.values())
            return permission in self.by_workflow.get(workflow_type, frozenset())
        return permission in self.general

    def has_any(self, *permissions: str, workflow_type: str | None = None) -> bool:
        return any(self.has(p, workflow_type) for p in permissions)

    def workflow_types_with(self, permission: str) -> tuple[str, ...]:
        return tuple(wf for wf in WORKFLOW_TYPES if self.has(permission, wf))

    def records_in(self, workflow_type: str) -> RecordScope | None:
        return self.records.get(workflow_type)

    def deal_scope(
        self,
        permission: str,
        user_id: uuid.UUID,
        *,
        wide_permission: str | None = None,
        own_only: bool = False,
    ) -> DealScope:
        """Область заявок для права.

        Без `wide_permission` (заявки): все заявки процесса, если записи процесса — `all`.
        С `wide_permission` (отчёты, `reports:all`): все — только при широком праве в
        процессе И записях `all`; иначе — только свои. `own_only` — только свои заявки
        в процессах с правом («мои заявки» бота).
        """
        rules: list[tuple[str, uuid.UUID | None]] = []
        for wf in WORKFLOW_TYPES:
            if not self.has(permission, wf):
                continue
            wide = (
                not own_only
                and self.records.get(wf) == "all"
                and (wide_permission is None or self.has(wide_permission, wf))
            )
            rules.append((wf, None if wide else user_id))
        return DealScope(rules=tuple(rules))

    def can_act_on_deal(
        self,
        permission: str,
        *,
        pipeline: str,
        responsible_user_id: uuid.UUID | None,
        user_id: uuid.UUID,
    ) -> bool:
        """Действие над заявкой: право в процессе и (чужие заявки или своя)."""
        if not self.has(permission, pipeline):
            return False
        return self.has("requests:write_any", pipeline) or responsible_user_id == user_id

    def transition_roles(self, pipeline: str) -> frozenset[str]:
        """Роли пользователя, которые могут выполнять переходы в процессе."""
        return self.transition_roles_by_workflow.get(pipeline, frozenset())

    @property
    def workflow_types(self) -> tuple[str, ...]:
        """Процессы, где пользователь видит заявки (`requests:read`)."""
        return self.workflow_types_with("requests:read")

    def as_me_payload(self, catalog: RoleCatalog | None = None) -> dict[str, Any]:
        """Блок `access` для `GET /auth/me` (api-contract.md §2.1)."""
        catalog = catalog or get_catalog_snapshot()
        active = [wf for wf in WORKFLOW_TYPES if wf in self.roles_by_workflow]
        return {
            "workflow_types": list(self.workflow_types),
            "records": {wf: self.records[wf] for wf in active if wf in self.records},
            "by_workflow": {wf: sorted(self.by_workflow.get(wf, ())) for wf in active},
            "transition_roles": {
                wf: sorted(self.transition_roles(wf)) for wf in active if self.transition_roles(wf)
            },
            "scoped_permissions": sorted(SCOPED_PERMISSIONS),
            "catalog_version": catalog.version,
        }


def compute_access(roles: Iterable[str], catalog: RoleCatalog) -> EffectiveAccess:
    """Чистое вычисление прав по кодам ролей и каталогу (роли не из каталога игнорируются)."""
    grants = [catalog.roles[code] for code in sorted(set(roles)) if code in catalog.roles]
    permissions: set[str] = set()
    general: set[str] = set()
    by_workflow: dict[str, set[str]] = {}
    roles_by_workflow: dict[str, set[str]] = {}
    transition_roles: dict[str, set[str]] = {}
    records: dict[str, RecordScope] = {}
    for grant in grants:
        permissions |= grant.permissions
        general |= grant.permissions - SCOPED_PERMISSIONS
        scoped = grant.permissions & SCOPED_PERMISSIONS
        for wf in WORKFLOW_TYPES:
            if wf not in grant.workflow_types:
                continue
            by_workflow.setdefault(wf, set()).update(scoped)
            roles_by_workflow.setdefault(wf, set()).add(grant.code)
            if "requests:transition" in grant.permissions:
                transition_roles.setdefault(wf, set()).add(grant.code)
            if grant.record_scope == "all":
                records[wf] = "all"
            else:
                records.setdefault(wf, "own")
    return EffectiveAccess(
        role_codes=tuple(grant.code for grant in grants),
        permissions=frozenset(permissions),
        by_workflow={wf: frozenset(v) for wf, v in by_workflow.items()},
        roles_by_workflow={wf: frozenset(v) for wf, v in roles_by_workflow.items()},
        records=records,
        general=frozenset(general),
        transition_roles_by_workflow={wf: frozenset(v) for wf, v in transition_roles.items()},
    )


def access_for_roles(roles: Iterable[str]) -> EffectiveAccess:
    """Права по снимку каталога; мемоизация по (версия каталога, роли) — LRU 256."""
    catalog = get_catalog_snapshot()
    key = (catalog.version, tuple(sorted(set(roles))))
    cached = _access_cache.get(key)
    if cached is not None:
        _access_cache.move_to_end(key)
        return cached
    access = compute_access(key[1], catalog)
    _access_cache[key] = access
    while len(_access_cache) > _ACCESS_CACHE_SIZE:
        _access_cache.popitem(last=False)
    return access


def _on_access_changed(_data: dict[str, Any]) -> None:
    reset_role_catalog()


# каждая реплика сбрасывает кэш каталога по событию из pub/sub (и при локальной доставке)
register_local_hook(SSE_ACCESS_CHANGED, _on_access_changed)
