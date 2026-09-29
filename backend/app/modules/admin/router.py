"""Админка: пользователи, настройки, фичефлаги, аудит (api-contract.md §10).

Справочники (`/admin/dictionaries*`) — модуль import_export; журнал интеграций
(`/admin/integration/*`) — модуль integrations. Изменение фичефлага публикует
SSE `flags.updated` — открытые интерфейсы перечитывают `/flags` без перезагрузки.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.db import get_session
from app.core.events import SSE_FLAGS_UPDATED
from app.core.pagination import ListParams, list_envelope, list_params
from app.core.security import require_roles
from app.core.sse import publish_event
from app.models.user import AppUser
from app.modules.admin import service
from app.modules.crm.deps import check_version

router = APIRouter(prefix="/admin", tags=["admin"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = Annotated[AppUser, Depends(require_roles("admin"))]


class UserPatch(BaseModel):
    """§10.2: через CRM меняется только иерархия эскалаций; роли — в Keycloak."""

    manager_id: uuid.UUID | None = None
    version: int | None = None


class SettingsPut(BaseModel):
    stuck_threshold_days: int | None = None
    stuck_escalate_to_manager: bool | None = None
    integration_mode: str | None = None
    file_delivery: str | None = None
    version: int | None = None


class FlagPatch(BaseModel):
    enabled: bool


# --------------------------------------------------------------------------------------
# Пользователи (§10.2)
# --------------------------------------------------------------------------------------


@router.get(
    "/users",
    summary="Пользователи (PG-зеркало Keycloak)",
    dependencies=[Depends(require_roles("admin", "head_kam"))],
)
async def list_users(
    session: SessionDep,
    params: Annotated[ListParams, Depends(list_params)],
    role: Annotated[str | None, Query(description="Фильтр по роли")] = None,
    search: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    items, total = await service.list_users(
        session, role=role, search=search, params=params
    )
    return list_envelope(items, total, params)


@router.patch("/users/{user_id}", summary="Назначить руководителя для эскалаций")
async def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    request: Request,
    session: SessionDep,
    admin: AdminUser,
) -> dict[str, Any]:
    user, previous = await service.set_manager(session, user_id, body.manager_id)
    if body.version is not None:
        check_version(user, body.version)
    await write_audit(
        session,
        actor_user_id=admin.id,
        action="user.manager_changed",
        entity_type="app_user",
        entity_id=user.id,
        before={"manager_id": str(previous) if previous else None},
        after={"manager_id": str(body.manager_id) if body.manager_id else None},
        ip=client_ip(request),
    )
    await session.commit()
    await session.refresh(user)
    return service.serialize_user(user)


@router.post("/users/sync", summary="Синхронизация зеркала из Keycloak Admin API")
async def sync_users(session: SessionDep, admin: AdminUser) -> dict[str, int]:
    return await service.sync_users_from_keycloak(session)


# --------------------------------------------------------------------------------------
# Настройки системы (§10.3)
# --------------------------------------------------------------------------------------


@router.get(
    "/settings", summary="Настройки системы", dependencies=[Depends(require_roles("admin"))]
)
async def get_settings(session: SessionDep) -> dict[str, Any]:
    return await service.get_system_settings(session)


@router.put("/settings", summary="Изменить настройки системы")
async def put_settings(
    body: SettingsPut, request: Request, session: SessionDep, admin: AdminUser
) -> dict[str, Any]:
    before = await service.get_system_settings(session)
    result = await service.put_system_settings(
        session, admin, body.model_dump(exclude_unset=True)
    )
    await write_audit(
        session,
        actor_user_id=admin.id,
        action="settings.updated",
        entity_type="app_setting",
        entity_id=None,
        before={k: v for k, v in before.items() if k != "version"},
        after={k: v for k, v in result.items() if k != "version"},
        ip=client_ip(request),
    )
    await session.commit()
    return result


# --------------------------------------------------------------------------------------
# Фичефлаги (§2.2; SSE flags.updated)
# --------------------------------------------------------------------------------------


@router.get(
    "/feature-flags",
    summary="Фичефлаги с описаниями",
    dependencies=[Depends(require_roles("admin"))],
)
async def feature_flags(session: SessionDep) -> dict[str, Any]:
    flags = await service.get_flags(session)
    return {"items": service.flags_catalog(flags)}


@router.patch("/feature-flags/{name}", summary="Переключить фичефлаг")
async def toggle_feature_flag(
    name: str,
    body: FlagPatch,
    request: Request,
    session: SessionDep,
    admin: AdminUser,
) -> dict[str, Any]:
    result = await service.set_flag(session, admin, name, body.enabled)
    await write_audit(
        session,
        actor_user_id=admin.id,
        action="feature_flag.toggled",
        entity_type="app_setting",
        entity_id=None,
        after={"name": name, "enabled": body.enabled},
        ip=client_ip(request),
    )
    await session.commit()
    await publish_event(SSE_FLAGS_UPDATED, {"name": name, "enabled": body.enabled})
    return result


# --------------------------------------------------------------------------------------
# Аудит (§10.4): фильтр action=student.pii_revealed — чип «Раскрытия ПДн» в UI
# --------------------------------------------------------------------------------------


@router.get(
    "/audit-log",
    summary="Сквозной журнал действий",
    dependencies=[Depends(require_roles("admin", "observer"))],
)
async def audit_log(
    session: SessionDep,
    params: Annotated[ListParams, Depends(list_params)],
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    items, total = await service.list_audit(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
        params=params,
    )
    return list_envelope(items, total, params)
