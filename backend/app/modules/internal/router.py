"""Внутренние джобы: CronJob «зависшие заявки» и отладочный прогон outbox.

Аутентификация — статический `X-Internal-Token` (constant-time, api-contract.md §11.1):
CronJob остаётся однострочным curl, Keycloak не нужен. Оба эндпоинта идемпотентны
и безопасны к параллельному запуску (SKIP LOCKED + dedup_key).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.outbox import run_relay_once
from app.core.security import require_internal_token
from app.models.deal import DealComment
from app.models.service import FileObject
from app.modules.files.storage import get_storage
from app.modules.workflow.service import check_stuck_requests

PURGE_AFTER_DAYS = 30

router = APIRouter(
    prefix="/internal/jobs",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "/check-stuck-requests",
    summary="Проверка зависших заявок (CronJob stuck-check, ежечасно)",
)
async def check_stuck(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await check_stuck_requests(session)


@router.post("/purge-deleted", summary="Физическая очистка soft-deleted (152-ФЗ, 30 дней)")
async def purge_deleted(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Физически удаляет мягко удалённое старше 30 дней.

    Студенты сюда не попадают: DELETE /students/{id} выполняет право на забвение
    сразу (в DDL `student` нет deleted_at). Джоба чистит `file_object` (вместе
    с объектами MinIO, best effort) и «(удалено)»-комментарии заявок.
    """
    started = time.monotonic()
    cutoff = datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)
    storage = get_storage()

    files = (
        (
            await session.execute(
                select(FileObject).where(
                    FileObject.deleted_at.is_not(None), FileObject.deleted_at < cutoff
                )
            )
        )
        .scalars()
        .all()
    )
    files_purged = 0
    for file_object in files:
        # объект в MinIO удаляем до строки: при сбое хранилища строка остаётся
        # и джоба доудалит объект на следующем прогоне
        if await storage.remove(file_object.s3_bucket, file_object.s3_key):
            await session.delete(file_object)
            files_purged += 1

    comments_result = await session.execute(
        delete(DealComment).where(
            DealComment.deleted_at.is_not(None), DealComment.deleted_at < cutoff
        )
    )
    await session.commit()
    return {
        "files_purged": files_purged,
        "files_pending": len(files) - files_purged,
        "comments_purged": comments_result.rowcount or 0,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


@router.post("/outbox-relay", summary="Принудительный прогон outbox-доставки (отладка)")
async def outbox_relay() -> dict[str, Any]:
    return await run_relay_once()
