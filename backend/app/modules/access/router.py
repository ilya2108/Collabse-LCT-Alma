"""API ролей и прав (api-contract.md §10.5).

- `GET /roles` — каталог ролей для любого пользователя CRM (подписи ролей, выбор ролей
  в переходах конструктора: `transition_workflow_types`);
- `/admin/permissions`, `/admin/roles*`, `PUT /admin/users/{id}/roles` — право
  `admin:roles`. Изменения применяются сразу: назначения читаются из БД на каждом
  запросе, каталог ролей сбрасывается на всех репликах по SSE `access.changed`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ensure_role_catalog
from app.core.audit import client_ip
from app.core.db import get_session
from app.core.rbac_catalog import SYSTEM_ROLE_CODES, WORKFLOW_TYPES
from app.core.security import get_current_user, require_permission
from app.models.user import AppUser
from app.modules.access import service
from app.modules.access.schemas import RoleCreate, RolePatch, RoleReset, UserRolesPut
from app.modules.admin.service import serialize_user

router = APIRouter(tags=["access"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RolesAdmin = Annotated[AppUser, Depends(require_permission("admin:roles"))]


@router.get("/roles", summary="Каталог ролей (подписи, роли для переходов процессов)")
async def role_catalog(
    session: SessionDep,
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    catalog = await ensure_role_catalog(session)
    # системные — в порядке кода (admin, head_kam, kam, observer), затем свои по названию
    system_order = {code: index for index, code in enumerate(SYSTEM_ROLE_CODES)}
    roles = sorted(
        catalog.roles.values(),
        key=lambda r: (
            (0, system_order.get(r.code, 0), "") if r.is_system else (1, 0, r.name.lower())
        ),
    )
    return {
        "items": [
            {
                "code": role.code,
                "name": role.name,
                "description": role.description,
                "is_system": role.is_system,
                "workflow_types": [wf for wf in WORKFLOW_TYPES if wf in role.workflow_types],
                "record_scope": role.record_scope,
                "transition_workflow_types": list(catalog.transition_workflow_types(role.code)),
            }
            for role in roles
        ]
    }


@router.get("/admin/permissions", summary="Каталог прав по группам (редактор ролей)")
async def permissions_catalog(_admin: RolesAdmin) -> dict[str, Any]:
    return {"groups": service.permission_groups()}


@router.get("/admin/roles", summary="Роли с правами и числом пользователей")
async def list_roles(session: SessionDep, _admin: RolesAdmin) -> dict[str, Any]:
    return {"items": await service.list_roles(session)}


@router.get("/admin/roles/{role_id}", summary="Роль: пользователи, покрытие переходов")
async def get_role(role_id: uuid.UUID, session: SessionDep, _admin: RolesAdmin) -> dict[str, Any]:
    return await service.get_role(session, role_id)


@router.post("/admin/roles", status_code=201, summary="Создать роль")
async def create_role(
    body: RoleCreate, request: Request, session: SessionDep, admin: RolesAdmin
) -> dict[str, Any]:
    return await service.create_role(session, actor=admin, body=body, ip=client_ip(request))


@router.patch("/admin/roles/{role_id}", summary="Изменить роль (права, область, название)")
async def update_role(
    role_id: uuid.UUID,
    body: RolePatch,
    request: Request,
    session: SessionDep,
    admin: RolesAdmin,
) -> dict[str, Any]:
    return await service.update_role(
        session, actor=admin, role_id=role_id, body=body, ip=client_ip(request)
    )


@router.post("/admin/roles/{role_id}/reset", summary="Сбросить системную роль к стандартным")
async def reset_role(
    role_id: uuid.UUID,
    body: RoleReset,
    request: Request,
    session: SessionDep,
    admin: RolesAdmin,
) -> dict[str, Any]:
    return await service.reset_system_role(
        session, actor=admin, role_id=role_id, version=body.version, ip=client_ip(request)
    )


@router.delete("/admin/roles/{role_id}", status_code=204, summary="Удалить свою роль")
async def delete_role(
    role_id: uuid.UUID, request: Request, session: SessionDep, admin: RolesAdmin
) -> Response:
    await service.delete_role(session, actor=admin, role_id=role_id, ip=client_ip(request))
    return Response(status_code=204)


@router.put("/admin/users/{user_id}/roles", summary="Назначить роли пользователю")
async def set_user_roles(
    user_id: uuid.UUID,
    body: UserRolesPut,
    request: Request,
    session: SessionDep,
    admin: RolesAdmin,
) -> dict[str, Any]:
    user = await service.set_user_roles(
        session,
        actor=admin,
        user_id=user_id,
        roles=body.roles,
        version=body.version,
        ip=client_ip(request),
    )
    await ensure_role_catalog(session)
    return serialize_user(user)
