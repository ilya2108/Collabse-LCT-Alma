"""Скачивание файлов MinIO через backend (api-contract.md §3.2).

RBAC — в `app.modules.files.service`: файл заявки (и вложение её комментария) вне области
роли — 404, как и сама заявка (`ensure_deal_file_visible`); права на тип файла —
`ensure_can_download`. Отдача — proxy-поток либо `302 presigned` (TTL 10 мин) по настройке
`FILE_DELIVERY`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.db import get_session
from app.core.errors import not_found
from app.core.security import get_current_user
from app.models.service import FileObject
from app.models.user import AppUser
from app.modules.files.service import (
    deliver_file,
    ensure_can_download,
    ensure_deal_file_visible,
)

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{file_id}/download", summary="Скачать файл (proxy или presigned)")
async def download_file(
    file_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> Response:
    file_object = await session.get(FileObject, file_id)
    if file_object is None or file_object.deleted_at is not None:
        raise not_found("Файл не найден")
    await ensure_deal_file_visible(session, user, file_object)  # вне области — 404
    ensure_can_download(user, file_object)
    return await deliver_file(file_object)
