"""Оркестрация сессий импорта: upload → распознавание → маппинг → валидация → apply.

api-contract.md §6, машина состояний — data-model.md §9.1:
`created → uploaded → parsed → mapping → preview → applying → completed | failed`,
`cancelled` — из любого нефинального. В API состояние `preview` отдаётся как
`validated` (§6.4). Исходный файл — MinIO bucket `crm-imports`; ПДн в сэмплах
и построчных ошибках маскируются до записи (data-model.md §8.5).
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import conflict, not_found, unprocessable, validation_error
from app.core.events import EVENT_IMPORT_FINISHED
from app.core.outbox import DirectRecipient, emit_event_notifications
from app.core.pii import get_pii_crypto, mask_email, mask_full_name, mask_phone
from app.models.service import FileObject, ImportRowError, ImportSession
from app.models.user import AppUser
from app.modules.files.service import store_bytes
from app.modules.files.storage import get_storage
from app.modules.import_export.importers import (
    PreparedRow,
    RowIssue,
    get_importer,
    prepare_context,
)
from app.modules.import_export.mapping import (
    EntityFields,
    MappingEntry,
    entity_fields,
    map_row,
    parse_mapping_entries,
    suggest_mapping,
    validate_mapping,
)
from app.modules.import_export.parsers import ParsedTable, detect_format, parse_table

logger = logging.getLogger(__name__)

MAX_ERRORS_IN_RESPONSE = 200
SAMPLES_PER_COLUMN = 3

# статусы, в которых сессию ещё можно менять/отменять
_EDITABLE_STATUSES = frozenset({"created", "uploaded", "parsed", "mapping", "preview"})

_MASKS = {"full_name": mask_full_name, "email": mask_email, "phone": mask_phone}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _no_data_rows_warning() -> dict[str, str]:
    """Файл из одних заголовков — не ошибка (ТЗ: не 500), но сигнал пользователю."""
    return {
        "code": "no_data_rows",
        "message": "В файле нет строк данных — только заголовки. Импортировать нечего",
    }


def api_state(status: str) -> str:
    """Проекция статуса БД в состояние API: `preview` → `validated` (§6.4)."""
    return "validated" if status == "preview" else status


def _mask_value(kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _MASKS[kind](value)


def mask_pii_values(
    values: dict[str, str | None], pii_fields: dict[str, str]
) -> dict[str, str | None]:
    """Маскирует значения ПДн-полей в mapped-строке (для ошибок/отчётов/сэмплов)."""
    return {
        key: _mask_value(pii_fields[key], value) if key in pii_fields else value
        for key, value in values.items()
    }


# --------------------------------------------------------------------------------------
# Создание сессии: upload + распознавание
# --------------------------------------------------------------------------------------


def _masked_samples(
    table: ParsedTable, spec: EntityFields, suggestions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Колонки с примерами значений; ПДн-подозрительные колонки маскируются сразу."""
    pii_fields = spec.pii_fields()
    pii_columns = {
        s["source_column"]: pii_fields[s["target_field"]]
        for s in suggestions
        if s["target_field"] in pii_fields
    }
    columns: list[dict[str, Any]] = []
    for index, name in enumerate(table.columns):
        samples: list[str] = []
        for row in table.rows:
            if len(samples) >= SAMPLES_PER_COLUMN:
                break
            value = row[index] if index < len(row) else None
            if value:
                kind = pii_columns.get(index)
                samples.append(_mask_value(kind, value) if kind else value)
        columns.append({"index": index, "name": name, "samples": samples})
    return columns


async def create_import_session(
    session: AsyncSession,
    *,
    data: bytes,
    file_name: str,
    entity_type: str,
    user: AppUser,
) -> dict[str, Any]:
    spec = entity_fields(entity_type)  # 400 на неподдерживаемый тип
    file_format = detect_format(data)
    # CPU-bound парсинг (openpyxl/xlrd) — вне event loop, как error-report/экспорт
    table = await asyncio.to_thread(parse_table, data, file_format)

    import_session = ImportSession(
        id=uuid.uuid4(),
        entity_type=entity_type,
        original_filename=file_name,
        file_format=file_format,
        detected_encoding=table.encoding,
        detected_columns=table.columns,
        status="mapping",
        created_by=user.id,
    )
    suggestions = suggest_mapping(table.columns, spec)
    sample_columns = _masked_samples(table, spec, suggestions)
    import_session.sample_rows = sample_columns[:]  # ПДн уже маскированы

    source_file = await store_bytes(
        session,
        data=data,
        original_name=file_name,
        content_type=None,
        bucket=get_settings().s3_bucket_imports,
        entity_type="import_session",
        entity_id=import_session.id,
        uploaded_by=user.id,
    )
    import_session.file_id = source_file.id
    session.add(import_session)
    await session.flush()
    await session.commit()

    warnings: list[dict[str, str]] = []
    if not table.rows:
        warnings.append(_no_data_rows_warning())

    return {
        "id": str(import_session.id),
        "entity_type": entity_type,
        "state": "mapping",
        "warnings": warnings,
        "file": {
            "file_name": file_name,
            "format": file_format,
            "size_bytes": len(data),
        },
        "detected": {
            "encoding": table.encoding,
            "encoding_confidence": table.encoding_confidence,
            "sheets": table.sheets,
            "active_sheet": table.active_sheet,
            "header_row": table.header_row,
            "columns": sample_columns,
        },
        "target_fields": [
            {"field": f.field, "label": f.label, "required": f.required}
            for f in spec.fields
        ],
        "suggested_mapping": suggestions,
    }


# --------------------------------------------------------------------------------------
# Доступ к сессии и сериализация
# --------------------------------------------------------------------------------------


async def get_owned_session(
    session: AsyncSession, session_id: uuid.UUID, user: AppUser
) -> ImportSession:
    """Сессии импорта приватны: видит автор, admin — все (§6.1)."""
    import_session = await session.get(ImportSession, session_id)
    if import_session is None:
        raise not_found("Сессия импорта не найдена")
    if "admin" not in (user.roles or []) and import_session.created_by != user.id:
        raise not_found("Сессия импорта не найдена")  # не раскрываем существование
    return import_session


def serialize_session(import_session: ImportSession) -> dict[str, Any]:
    spec = entity_fields(import_session.entity_type)
    return {
        "id": str(import_session.id),
        "entity_type": import_session.entity_type,
        "state": api_state(import_session.status),
        "file": {
            "file_name": import_session.original_filename,
            "format": import_session.file_format,
            "file_id": str(import_session.file_id) if import_session.file_id else None,
        },
        "detected": {
            "encoding": import_session.detected_encoding,
            "columns": import_session.sample_rows or [],
        },
        "target_fields": [
            {"field": f.field, "label": f.label, "required": f.required}
            for f in spec.fields
        ],
        "mapping": (import_session.column_mapping or {}).get("mapping"),
        "options": import_session.options or {},
        "stats": import_session.stats,
        "error_message": import_session.error_message,
        "created_at": import_session.created_at.isoformat()
        if import_session.created_at
        else None,
        "finished_at": import_session.finished_at.isoformat()
        if import_session.finished_at
        else None,
    }


# --------------------------------------------------------------------------------------
# Маппинг
# --------------------------------------------------------------------------------------


async def save_mapping(
    session: AsyncSession,
    import_session: ImportSession,
    *,
    raw_mapping: list[dict[str, Any]],
    raw_options: dict[str, Any],
) -> dict[str, Any]:
    if import_session.status not in _EDITABLE_STATUSES:
        raise conflict("Сессия импорта уже завершена, маппинг менять нельзя")
    spec = entity_fields(import_session.entity_type)
    entries = parse_mapping_entries(raw_mapping)
    columns_count = len(import_session.detected_columns or [])
    validate_mapping(entries, columns_count, spec)
    import_session.column_mapping = {"mapping": raw_mapping}
    import_session.options = dict(raw_options or {})
    import_session.status = "mapping"
    await session.commit()
    return serialize_session(import_session)


def _stored_entries(import_session: ImportSession) -> list[MappingEntry]:
    raw = (import_session.column_mapping or {}).get("mapping")
    if not raw:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "mapping",
                    "code": "mapping_not_set",
                    "message": "Сначала сохраните маппинг колонок (PUT …/mapping)",
                }
            ],
        )
    return parse_mapping_entries(raw)


# --------------------------------------------------------------------------------------
# Общий конвейер: чтение исходника → mapped-строки → валидация
# --------------------------------------------------------------------------------------


async def _load_source(session: AsyncSession, import_session: ImportSession) -> bytes:
    if import_session.file_id is None:
        raise conflict("У сессии импорта нет исходного файла")
    file_object = await session.get(FileObject, import_session.file_id)
    if file_object is None:
        raise conflict("Исходный файл сессии импорта утерян")
    return await get_storage().read_bytes(file_object.s3_bucket, file_object.s3_key)


async def _prepare_rows(
    session: AsyncSession, import_session: ImportSession, user: AppUser
) -> tuple[list[PreparedRow], Any]:
    """Полный dry-run: парсинг с опциями, маппинг, построчная валидация."""
    entries = _stored_entries(import_session)
    options = import_session.options or {}
    data = await _load_source(session, import_session)
    # CPU-bound парсинг (openpyxl/xlrd) — вне event loop (dry-run гоняет его повторно)
    table = await asyncio.to_thread(
        parse_table,
        data,
        import_session.file_format,
        sheet=options.get("sheet", 0),
        header_row=int(options.get("header_row", 0)),
        encoding=options.get("encoding") or None,
    )
    if options.get("encoding"):
        import_session.detected_encoding = options["encoding"]

    ctx = await prepare_context(
        session,
        entity_type=import_session.entity_type,
        entries=entries,
        raw_options=options,
        actor=user,
        crypto=get_pii_crypto(),
        import_session_id=import_session.id,
    )
    default_values = ctx.options.default_values
    skip_empty = ctx.options.skip_empty_rows

    prepared: list[PreparedRow] = []
    header_offset = table.header_row + 2  # 1-based номер первой строки данных
    for i, raw_row in enumerate(table.rows):
        if skip_empty and not any(v for v in raw_row):
            continue
        values, map_errors = map_row(raw_row, entries, default_values)
        # для JSON с пропущенными элементами номера строк идут по исходному файлу
        row_number = table.row_numbers[i] if table.row_numbers else header_offset + i
        row = PreparedRow(row_number=row_number, values=values)
        for err in map_errors:
            row.issues.append(
                RowIssue(
                    row=row.row_number,
                    column=err.get("column"),
                    code=err.get("code", "invalid_value"),
                    message=err.get("message", "Некорректное значение"),
                )
            )
        prepared.append(row)

    importer = get_importer(import_session.entity_type)
    await importer.validate(ctx, prepared)
    # ошибки уровня парсинга (null/не-объектные элементы JSON) — после validate,
    # чтобы пустые строки-заглушки не собирали каскад required-ошибок
    for parse_issue in table.row_issues:
        prepared.append(
            PreparedRow(
                row_number=int(parse_issue["row"]),
                values={},
                issues=[
                    RowIssue(
                        row=int(parse_issue["row"]),
                        column=None,
                        code=str(parse_issue.get("code") or "row_error"),
                        message=str(parse_issue.get("message") or "Строка не распознана"),
                    )
                ],
            )
        )
    return prepared, ctx


async def _persist_row_errors(
    session: AsyncSession,
    import_session: ImportSession,
    rows: list[PreparedRow],
    pii_fields: dict[str, str],
) -> list[dict[str, Any]]:
    """Перезаписывает `import_row_error` (ПДн маскированы) и возвращает плоский список."""
    await session.execute(
        ImportRowError.__table__.delete().where(
            ImportRowError.import_session_id == import_session.id
        )
    )
    flat: list[dict[str, Any]] = []
    for row in rows:
        if row.valid:
            continue
        masked = mask_pii_values(row.values, pii_fields)
        for issue in row.issues:
            flat.append(issue.as_dict())
        session.add(
            ImportRowError(
                import_session_id=import_session.id,
                row_number=row.row_number,
                raw_data=masked,
                error="; ".join(i.message for i in row.issues),
            )
        )
    flat.sort(key=lambda item: item["row"])
    return flat


def _build_error_report_xlsx(
    rows: list[PreparedRow], pii_fields: dict[str, str]
) -> bytes:
    """XLSX-отчёт об ошибках (§6.4): строка, колонка, код, сообщение, данные (маскированы)."""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Ошибки импорта"
    sheet.append(["Строка", "Колонка", "Код", "Ошибка", "Данные строки (ПДн маскированы)"])
    for row in rows:
        if row.valid:
            continue
        masked = mask_pii_values(row.values, pii_fields)
        data_str = "; ".join(f"{k}={v}" for k, v in masked.items() if v is not None)
        for issue in row.issues:
            sheet.append([issue.row, issue.column, issue.code, issue.message, data_str])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def _store_error_report(
    session: AsyncSession,
    import_session: ImportSession,
    rows: list[PreparedRow],
    pii_fields: dict[str, str],
    user: AppUser,
) -> uuid.UUID | None:
    if all(row.valid for row in rows):
        return None
    data = await asyncio.to_thread(_build_error_report_xlsx, rows, pii_fields)
    report = await store_bytes(
        session,
        data=data,
        original_name=f"import_errors_{import_session.id.hex[:8]}.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        bucket=get_settings().s3_bucket_imports,
        entity_type="import_session",
        entity_id=import_session.id,
        uploaded_by=user.id,
    )
    return report.id


# --------------------------------------------------------------------------------------
# Валидация (dry-run) и применение
# --------------------------------------------------------------------------------------


async def run_validation(
    session: AsyncSession, import_session: ImportSession, user: AppUser
) -> dict[str, Any]:
    if import_session.status not in _EDITABLE_STATUSES:
        raise conflict("Сессия импорта уже завершена")
    rows, ctx = await _prepare_rows(session, import_session, user)
    pii_fields = ctx.spec.pii_fields()
    errors = await _persist_row_errors(session, import_session, rows, pii_fields)
    report_file_id = await _store_error_report(session, import_session, rows, pii_fields, user)

    valid_count = sum(1 for r in rows if r.valid)
    import_session.status = "preview"
    import_session.stats = {
        "total": len(rows),
        "valid": valid_count,
        "errors": len(rows) - valid_count,
        "error_report_file_id": str(report_file_id) if report_file_id else None,
    }
    await session.commit()
    return {
        "state": "validated",
        "total_rows": len(rows),
        "valid_rows": valid_count,
        "error_rows": len(rows) - valid_count,
        "warnings": [] if rows else [_no_data_rows_warning()],
        "errors": errors[:MAX_ERRORS_IN_RESPONSE],
        "errors_truncated": len(errors) > MAX_ERRORS_IN_RESPONSE,
        "error_report_file_id": str(report_file_id) if report_file_id else None,
    }


async def _mark_session_failed(import_session_id: uuid.UUID) -> None:
    """Фиксирует status='failed' в ОТДЕЛЬНОЙ сессии/соединении.

    Ошибка применения (например, InterfaceError asyncpg) может инвалидировать
    исходное соединение — тогда commit статуса на нём откатывается вместе
    с 500-ответом, и сессия навсегда остаётся в validated. Свежая сессия из
    фабрики не зависит от судьбы сломанного коннекта.
    """
    from app.core.db import get_session_factory

    try:
        async with get_session_factory()() as recovery:
            fresh = await recovery.get(ImportSession, import_session_id)
            if fresh is not None and fresh.status not in ("completed", "cancelled"):
                fresh.status = "failed"
                fresh.error_message = "Ошибка применения импорта"
                fresh.finished_at = _now()
                await recovery.commit()
    except Exception:  # noqa: BLE001 — статус вторичен, исходную ошибку не маскируем
        logger.exception("Импорт %s: не удалось сохранить статус failed", import_session_id)


async def run_apply(
    session: AsyncSession,
    import_session: ImportSession,
    user: AppUser,
    *,
    mode: str,
) -> dict[str, Any]:
    if import_session.status == "completed":
        # идемпотентный повтор (Idempotency-Key/ретрай UI): вернуть сохранённый результат
        return {
            "state": "completed",
            "result": import_session.stats or {},
            "applied_at": import_session.finished_at.isoformat()
            if import_session.finished_at
            else None,
        }
    if import_session.status == "applying":
        raise conflict("Импорт уже выполняется")
    if import_session.status not in _EDITABLE_STATUSES:
        raise conflict("Сессия импорта отменена или завершилась ошибкой")

    rows, ctx = await _prepare_rows(session, import_session, user)
    pii_fields = ctx.spec.pii_fields()
    error_rows = [r for r in rows if not r.valid]
    if mode == "all_or_nothing" and error_rows:
        await _persist_row_errors(session, import_session, rows, pii_fields)
        import_session.status = "preview"
        await session.commit()
        raise unprocessable(
            f"Импорт в режиме «всё или ничего» отклонён: ошибок в файле — {len(error_rows)}"
        )

    import_session.status = "applying"
    await session.flush()
    try:
        importer = get_importer(import_session.entity_type)
        stats = await importer.apply(ctx, [r for r in rows if r.valid])
    except Exception:
        logger.exception("Импорт %s: ошибка применения", import_session.id)
        await session.rollback()
        await _mark_session_failed(import_session.id)
        raise

    await _persist_row_errors(session, import_session, rows, pii_fields)
    report_file_id = await _store_error_report(session, import_session, rows, pii_fields, user)
    result = {
        **stats.as_dict(),
        "failed": len(error_rows),
        "report_file_id": str(report_file_id) if report_file_id else None,
    }
    import_session.status = "completed"
    import_session.stats = result
    import_session.finished_at = _now()
    await write_audit(
        session,
        actor_user_id=user.id,
        action="import.applied",
        entity_type="import_session",
        entity_id=import_session.id,
        after={"entity_type": import_session.entity_type, **stats.as_dict()},
    )
    await emit_event_notifications(
        session,
        event_type=EVENT_IMPORT_FINISHED,
        entity_type="import_session",
        entity_id=import_session.id,
        subject="Импорт завершён",
        body=(
            f"Импорт «{import_session.original_filename}» завершён: "
            f"создано {stats.created}, обновлено {stats.updated}, "
            f"пропущено {stats.skipped}, ошибок {len(error_rows)}"
        ),
        payload={"import_session_id": str(import_session.id)},
        direct_recipients=[DirectRecipient(user=user)],
    )
    await session.commit()
    return {
        "state": "completed",
        "result": result,
        "applied_at": import_session.finished_at.isoformat(),
    }


async def cancel_session(
    session: AsyncSession, import_session: ImportSession
) -> dict[str, Any]:
    if import_session.status not in _EDITABLE_STATUSES:
        raise conflict("Сессию в финальном состоянии отменить нельзя")
    import_session.status = "cancelled"
    import_session.finished_at = _now()
    await session.commit()
    return {"state": "cancelled"}


async def list_sessions(
    session: AsyncSession, user: AppUser, *, state: str | None
) -> list[dict[str, Any]]:
    stmt = select(ImportSession).order_by(ImportSession.created_at.desc()).limit(100)
    if "admin" not in (user.roles or []):
        stmt = stmt.where(ImportSession.created_by == user.id)
    if state:
        db_status = "preview" if state == "validated" else state
        stmt = stmt.where(ImportSession.status == db_status)
    rows = (await session.execute(stmt)).scalars().all()
    return [serialize_session(row) for row in rows]
