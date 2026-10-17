"""Общие помощники CRUD справочников: видимость, optimistic locking, выборка по id."""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import forbidden, not_found, stale_version
from app.models.base import Base
from app.models.user import AppUser

ModelT = TypeVar("ModelT", bound=Base)


def ensure_can_write_owned(user: AppUser, owner_id: uuid.UUID | None) -> None:
    """Правило §12.2: без `crm:write_any` (КАМ) писать можно только объекты, где
    пользователь ответственный (или ответственный ещё не назначен)."""
    if user.access.has("crm:write_any"):
        return
    if owner_id is not None and owner_id != user.id:
        raise forbidden("Изменять можно только объекты, где вы ответственный")


async def get_or_404(
    session: AsyncSession,
    model: type[ModelT],
    object_id: uuid.UUID,
    message: str = "Объект не найден",
) -> ModelT:
    obj = await session.get(model, object_id)
    if obj is None:
        raise not_found(message)
    return obj


def check_version(obj: object, version: int) -> None:
    """Сверка optimistic-lock версии (xmin) до применения PATCH; иначе 409 stale_version."""
    current = getattr(obj, "xmin", None)
    if current is not None and current != version:
        raise stale_version()
