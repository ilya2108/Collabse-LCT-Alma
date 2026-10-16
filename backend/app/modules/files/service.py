"""Файлы: сохранение `file_object` + объект MinIO, RBAC скачивания, отдача.

api-contract.md §3.2: скачивание любого файла — `GET /files/{file_id}/download`,
backend проверяет RBAC и отдаёт proxy-поток либо `302` на presigned URL (TTL 10 мин),
режим `FILE_DELIVERY=proxy|presigned` (по умолчанию proxy — закрытый контур).

Права (F2): чужие импорты и выгрузки — `files:others`; файлы договоров, студентов и
заявок — `files:business`; файлы заявки и её комментариев вне области роли (процесс
B2B/B2C, «только свои») не существуют для пользователя — 404 (`ensure_deal_file_visible`).
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone

from fastapi import UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.config import get_settings
from app.core.errors import ApiError, forbidden, not_found, validation_error
from app.models.deal import Deal, DealComment
from app.models.service import FileObject
from app.models.user import AppUser
from app.modules.files.storage import build_s3_key, content_disposition, get_storage

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  # вложения (api-contract.md §1.4)
MAX_IMPORT_BYTES = 20 * 1024 * 1024  # файлы импорта

# файлы, приватные для автора (с правом files:others — все): исходники/отчёты импорта
# и выгрузки
_OWNER_ONLY_ENTITY_TYPES = frozenset({"import_session", "report"})
# без права files:business (наблюдатель) скачивание закрыто (api-contract.md §12.2):
# договоры, студенты (Р-19), заявки и вложения комментариев
_BUSINESS_ENTITY_TYPES = frozenset({"contract", "student", "deal", "deal_comment"})
_DEAL_ENTITY_TYPES = frozenset({"deal", "deal_comment"})


def file_too_large(limit_bytes: int) -> ApiError:
    return ApiError(
        413,
        "file_too_large",
        f"Файл превышает допустимый размер {limit_bytes // (1024 * 1024)} МБ",
    )


def guess_content_type(upload: UploadFile) -> str:
    if upload.content_type and upload.content_type != "application/octet-stream":
        return upload.content_type
    guessed, _ = mimetypes.guess_type(upload.filename or "")
    return guessed or "application/octet-stream"


async def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Читает multipart-файл целиком с контролем лимита (413 file_too_large)."""
    data = await upload.read()
    if len(data) > max_bytes:
        raise file_too_large(max_bytes)
    if not data:
        raise validation_error(
            "Ошибка валидации данных",
            details=[{"field": "file", "code": "empty_file", "message": "Файл пуст"}],
        )
    return data


async def store_bytes(
    session: AsyncSession,
    *,
    data: bytes,
    original_name: str,
    content_type: str | None,
    bucket: str,
    entity_type: str,
    entity_id: uuid.UUID,
    uploaded_by: uuid.UUID | None,
) -> FileObject:
    """Загружает объект в MinIO и добавляет `file_object` в ТЕКУЩУЮ сессию (без commit).

    MinIO-запись выполняется до вставки строки: при недоступности хранилища
    транзакция не фиксирует «висячие» метаданные.
    """
    file_id = uuid.uuid4()
    s3_key = build_s3_key(entity_type, entity_id, file_id, original_name)
    await get_storage().put_bytes(bucket, s3_key, data, content_type)
    file_object = FileObject(
        id=file_id,
        s3_bucket=bucket,
        s3_key=s3_key,
        original_name=original_name,
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        entity_type=entity_type,
        entity_id=entity_id,
        uploaded_by=uploaded_by,
    )
    session.add(file_object)
    await session.flush()
    return file_object


async def store_upload(
    session: AsyncSession,
    *,
    upload: UploadFile,
    bucket: str,
    entity_type: str,
    entity_id: uuid.UUID,
    uploaded_by: uuid.UUID | None,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> FileObject:
    data = await read_upload(upload, max_bytes)
    return await store_bytes(
        session,
        data=data,
        original_name=upload.filename or "file",
        content_type=guess_content_type(upload),
        bucket=bucket,
        entity_type=entity_type,
        entity_id=entity_id,
        uploaded_by=uploaded_by,
    )


def ensure_can_download(user: AppUser, file_object: FileObject) -> None:
    """RBAC скачивания (§12.1): без `files:business` — без файлов договоров/студентов/
    заявок; исходники импорта и выгрузки видят автор и владельцы `files:others`."""
    entity_type = file_object.entity_type or "other"
    if entity_type in _OWNER_ONLY_ENTITY_TYPES:
        if not user.access.has("files:others") and file_object.uploaded_by != user.id:
            raise forbidden("Файл доступен только его автору")
        return
    if entity_type in _BUSINESS_ENTITY_TYPES and not user.access.has("files:business"):
        raise forbidden("Вашей роли скачивание этих файлов недоступно")


async def ensure_deal_file_visible(
    session: AsyncSession, user: AppUser, file_object: FileObject
) -> None:
    """Файл заявки (или вложение её комментария) вне области роли — 404, как и сама
    заявка (Р-F2-10). Прочие файлы не проверяет."""
    entity_type = file_object.entity_type or "other"
    if entity_type not in _DEAL_ENTITY_TYPES or file_object.entity_id is None:
        return
    deal_id = file_object.entity_id
    if entity_type == "deal_comment":
        comment = await session.get(DealComment, file_object.entity_id)
        if comment is None:
            raise not_found("Файл не найден")
        deal_id = comment.deal_id
    deal = await session.get(Deal, deal_id)
    scope = user.access.deal_scope("requests:read", user.id)
    if deal is None or not scope.allows(deal.pipeline, deal.responsible_user_id):
        raise not_found("Файл не найден")


async def deliver_file(file_object: FileObject) -> Response:
    """Отдача файла по режиму FILE_DELIVERY: proxy-поток или 302 на presigned URL."""
    settings = get_settings()
    if settings.file_delivery == "presigned":
        url = await get_storage().presigned_url(
            file_object.s3_bucket, file_object.s3_key, file_object.original_name
        )
        return RedirectResponse(url, status_code=302)
    stream = get_storage().stream(file_object.s3_bucket, file_object.s3_key)
    headers = {"Content-Disposition": content_disposition(file_object.original_name)}
    if file_object.size_bytes:
        headers["Content-Length"] = str(file_object.size_bytes)
    return StreamingResponse(
        stream,
        media_type=file_object.content_type or "application/octet-stream",
        headers=headers,
    )


def soft_delete_file(file_object: FileObject) -> None:
    """Мягкое удаление: объект в MinIO чистит `POST /internal/jobs/purge-deleted`."""
    file_object.deleted_at = datetime.now(timezone.utc)
