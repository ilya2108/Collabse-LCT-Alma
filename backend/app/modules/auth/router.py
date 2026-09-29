"""`GET /auth/me` — профиль и permissions для отрисовки UI по ролевой модели."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.security import get_current_user
from app.models.user import AppUser
from app.modules.auth.permissions import permissions_for_roles

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramLink(BaseModel):
    linked: bool
    tg_username: str | None = None
    linked_at: datetime | None = None


class MeResponse(BaseModel):
    id: UUID
    username: str
    full_name: str | None
    email: str | None
    roles: list[str]
    manager_id: UUID | None
    permissions: list[str]
    telegram: TelegramLink


@router.get("/me", response_model=MeResponse, summary="Профиль текущего пользователя")
async def get_me(user: Annotated[AppUser, Depends(get_current_user)]) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        roles=sorted(user.roles),
        manager_id=user.manager_id,
        permissions=permissions_for_roles(user.roles),
        telegram=TelegramLink(
            linked=user.tg_chat_id is not None,
            tg_username=user.tg_username,
            linked_at=user.tg_linked_at,
        ),
    )
