"""`GET /auth/me` — профиль, роли и эффективные права для отрисовки UI.

Права и область B2B/B2C вычисляются по ролям из БД Альмы (`user.access`,
app.core.access); фронт перестраивает интерфейс по SSE `access.changed`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.access import get_catalog_snapshot
from app.core.security import get_current_user
from app.models.user import AppUser

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramLink(BaseModel):
    linked: bool
    tg_username: str | None = None
    linked_at: datetime | None = None


class RoleRef(BaseModel):
    code: str
    name: str
    is_system: bool


class AccessOut(BaseModel):
    workflow_types: list[Literal["b2b", "b2c"]]
    records: dict[str, Literal["all", "own"]]
    by_workflow: dict[str, list[str]]
    transition_roles: dict[str, list[str]]
    scoped_permissions: list[str]
    catalog_version: int


class MeResponse(BaseModel):
    id: UUID
    username: str
    full_name: str | None
    email: str | None
    roles: list[str]
    role_details: list[RoleRef]
    manager_id: UUID | None
    permissions: list[str]
    access: AccessOut
    telegram: TelegramLink


@router.get("/me", response_model=MeResponse, summary="Профиль текущего пользователя")
async def get_me(user: Annotated[AppUser, Depends(get_current_user)]) -> MeResponse:
    catalog = get_catalog_snapshot()
    access = user.access
    return MeResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        roles=list(access.role_codes),
        role_details=[
            RoleRef(
                code=code,
                name=catalog.roles[code].name,
                is_system=catalog.roles[code].is_system,
            )
            for code in access.role_codes
        ],
        manager_id=user.manager_id,
        permissions=sorted(access.permissions),
        access=AccessOut(**access.as_me_payload(catalog)),
        telegram=TelegramLink(
            linked=user.tg_chat_id is not None,
            tg_username=user.tg_username,
            linked_at=user.tg_linked_at,
        ),
    )
