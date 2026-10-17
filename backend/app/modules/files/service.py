"""Файлы: сохранение `file_object` + объект MinIO, RBAC скачивания, отдача.

api-contract.md §3.2: скачивание любого файла — `GET /files/{file_id}/download`,
backend проверяет RBAC и отдаёт proxy-поток либо `302` на presigned URL (TTL 10 мин),
режим `FILE_DELIVERY=proxy|presigned` (по умолчанию proxy — закрытый контур).

Права (F2): чужие импорты и выгрузки — `files:others`; файлы договоров, студентов и
заявок — `files:business`; файлы заявки и её комментариев вне области роли (процесс
B2B/B2C, «только свои») не существуют для пользователя — 404 (`ensure_deal_file_visible`).

Вложения заявки (файлы в статусах) проходят белый список `check_attachment_format`:
10 форматов ТЗ по расширению и сигнатуре, иначе 400 `file_type_not_allowed` в деталях.
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


# --- белый список вложений заявки (ТЗ, ФТ3: 10 форматов) --------------------------------------
# расширение → (тип содержимого, допустимые сигнатуры начала файла). Тип отдачи задаёт
# сервер, а не заголовок клиента: «отчёт.pdf» с text/html скачается как PDF, не откроется
# страницей. Сигнатура сверяется с расширением — переименованный exe/html не пройдёт.
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE2_MAGIC = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)  # doc, xls (Office 97–2003)
_GZIP_MAGIC = (b"\x1f\x8b",)
ATTACHMENT_FORMATS: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "pdf": ("application/pdf", (b"%PDF-",)),
    "zip": ("application/zip", _ZIP_MAGIC),
    "gz": ("application/gzip", _GZIP_MAGIC),
    "gzip": ("application/gzip", _GZIP_MAGIC),
    "tgz": ("application/gzip", _GZIP_MAGIC),
    "rar": ("application/vnd.rar", (b"Rar!\x1a\x07",)),
    # Word сохраняет .doc и как RTF — открывается тем же Word
    "doc": ("application/msword", (*_OLE2_MAGIC, b"{\\rtf")),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _ZIP_MAGIC,
    ),
    "xls": ("application/vnd.ms-excel", _OLE2_MAGIC),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _ZIP_MAGIC,
    ),
}
ATTACHMENT_FORMATS_LABEL = "PNG, JPEG, PDF, ZIP, GZIP, RAR, DOC, DOCX, XLS, XLSX"
# PDF допускает мусор до заголовка (до 1 КБ) — так пишут некоторые сканеры
_PDF_HEADER_WINDOW = 1024


def _file_type_error(message: str) -> ApiError:
    # общий текст + причина в деталях: тост показывает обе строки без повтора
    return validation_error(
        "Недопустимый формат файла",
        details=[{"field": "file", "code": "file_type_not_allowed", "message": message}],
    )


def attachment_extension(file_name: str) -> str:
    """Расширение имени файла в нижнем регистре без точки (`архив.tar.gz` → `gz`)."""
    _stem, dot, ext = (file_name or "").rpartition(".")
    return ext.lower() if dot else ""


def check_attachment_format(file_name: str, data: bytes) -> str:
    """Белый список вложений заявки: расширение из 10 форматов ТЗ и сигнатура содержимого.

    Возвращает тип содержимого для хранения и отдачи; иначе — 400 с кодом детали
    `file_type_not_allowed` и понятным текстом (какие форматы можно приложить).
    """
    ext = attachment_extension(file_name)
    spec = ATTACHMENT_FORMATS.get(ext)
    if spec is None:
        shown = f"«.{ext}»" if ext else "без расширения"
        raise _file_type_error(
            f"Формат {shown} не поддерживается. Можно приложить: {ATTACHMENT_FORMATS_LABEL}"
        )
    content_type, signatures = spec
    if ext == "pdf":
        matches = b"%PDF-" in data[:_PDF_HEADER_WINDOW]
    else:
        matches = data.startswith(signatures)
    if not matches:
        raise _file_type_error(
            f"Содержимое файла «{file_name}» не похоже на {ext.upper()} — "
            "проверьте, что расширение не переименовано"
        )
    return content_type


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
    stage_history_id: uuid.UUID | None = None,
) -> FileObject:
    """Загружает объект в MinIO и добавляет `file_object` в ТЕКУЩУЮ сессию (без commit).

    MinIO-запись выполняется до вставки строки: при недоступности хранилища
    транзакция не фиксирует «висячие» метаданные. `stage_history_id` — только у файлов
    заявки: строка истории (переход или вход в этап), к которой файл приложен.
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
        stage_history_id=stage_history_id,
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
    check_format: bool = False,
    stage_history_id: uuid.UUID | None = None,
) -> FileObject:
    """Multipart-файл → MinIO + `file_object`. `check_format` — белый список вложений
    заявки (`check_attachment_format`): тип содержимого тогда задаёт сервер."""
    data = await read_upload(upload, max_bytes)
    original_name = upload.filename or "file"
    content_type = (
        check_attachment_format(original_name, data) if check_format else guess_content_type(upload)
    )
    return await store_bytes(
        session,
        data=data,
        original_name=original_name,
        content_type=content_type,
        bucket=bucket,
        entity_type=entity_type,
        entity_id=entity_id,
        uploaded_by=uploaded_by,
        stage_history_id=stage_history_id,
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
    headers = {
        "Content-Disposition": content_disposition(file_object.original_name),
        # вложение не исполняется в домене CRM: тип не угадывается браузером, а открытый
        # напрямую HTML/SVG попадает в песочницу без скриптов и доступа к origin
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    }
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
