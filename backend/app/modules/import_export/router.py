"""Импорт (5-шаговый конвейер), экспорт и справочники админки.

Контракт — api-contract.md §6–7, §10.1. Импорт: upload → распознавание (magic
bytes + кодировки) → интерактивный маппинг → dry-run валидация → применение.
Экспорт: XLSX/CSV/JSON с выбором кодировки (utf-8 BOM / cp1251). Справочники —
только admin (разовая предзагрузка «через диалоговое окно», JSON или таблицы).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.db import get_session
from app.core.errors import forbidden
from app.core.security import get_current_user, require_roles
from app.models.user import AppUser
from app.modules.files.service import MAX_IMPORT_BYTES, deliver_file, read_upload
from app.modules.import_export import dictionaries as dictionaries_service
from app.modules.import_export import export as export_service
from app.modules.import_export import service as import_service
from app.modules.import_export.schemas import (
    ApplyBody,
    DictionaryImportBody,
    DictionaryItemBody,
    ExportJobBody,
    MappingBody,
)

router = APIRouter(tags=["import-export"])

_ADMIN = Depends(require_roles("admin"))


# --- импорт -----------------------------------------------------------------------------


@router.post("/import/sessions", status_code=201, summary="Создать сессию импорта (файл)")
async def create_import_session(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    file: Annotated[UploadFile, File(description="XLSX/XLS/CSV/JSON, до 20 МБ")],
    entity_type: Annotated[str, Form()],
) -> dict[str, Any]:
    if entity_type.startswith("dictionary:") and "admin" not in (user.roles or []):
        # импорт справочников — только admin (§6.1, §10.1)
        raise forbidden("Импорт справочников доступен только администратору")
    data = await read_upload(file, MAX_IMPORT_BYTES)
    return await import_service.create_import_session(
        session,
        data=data,
        file_name=file.filename or "import.dat",
        entity_type=entity_type,
        user=user,
    )


@router.get("/import/sessions", summary="Мои сессии импорта (admin видит все)")
async def list_import_sessions(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    state: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    items = await import_service.list_sessions(session, user, state=state)
    return {"items": items, "total": len(items)}


@router.get("/import/sessions/{session_id}", summary="Состояние сессии импорта")
async def get_import_session(
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> dict[str, Any]:
    import_session = await import_service.get_owned_session(session, session_id, user)
    return import_service.serialize_session(import_session)


@router.put("/import/sessions/{session_id}/mapping", summary="Сохранить интерактивный маппинг")
async def save_mapping(
    session_id: uuid.UUID,
    body: MappingBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> dict[str, Any]:
    import_session = await import_service.get_owned_session(session, session_id, user)
    return await import_service.save_mapping(
        session,
        import_session,
        raw_mapping=[item.model_dump() for item in body.mapping],
        raw_options=body.options.model_dump(),
    )


@router.post("/import/sessions/{session_id}/validate", summary="Dry-run валидация импорта")
async def validate_import(
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> dict[str, Any]:
    import_session = await import_service.get_owned_session(session, session_id, user)
    return await import_service.run_validation(session, import_session, user)


@router.post("/import/sessions/{session_id}/apply", summary="Применить импорт")
async def apply_import(
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    body: ApplyBody | None = None,
) -> dict[str, Any]:
    import_session = await import_service.get_owned_session(session, session_id, user)
    mode = body.mode if body is not None else "skip_errors"
    return await import_service.run_apply(session, import_session, user, mode=mode)


@router.delete("/import/sessions/{session_id}", summary="Отменить сессию импорта")
async def cancel_import(
    session_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> dict[str, Any]:
    import_session = await import_service.get_owned_session(session, session_id, user)
    return await import_service.cancel_session(session, import_session)


# --- экспорт ----------------------------------------------------------------------------


@router.post("/export/jobs", status_code=201, summary="Создать выгрузку (XLSX/CSV/JSON)")
async def create_export_job(
    body: ExportJobBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    # RBAC §12.1: экспорт доступен всем ролям, observer получает маскированные ПДн
    return await export_service.create_export_job(
        session,
        user=user,
        entity_type=body.entity_type,
        export_format=body.format,
        filters=body.filters,
        columns=body.columns,
        options=body.options.model_dump(),
    )


@router.get("/export/jobs", summary="Мои выгрузки")
async def list_export_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    items = await export_service.list_export_jobs(session, user)
    return {"items": items, "total": len(items)}


@router.get("/export/jobs/{job_id}", summary="Статус выгрузки")
async def get_export_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    payload, _file = await export_service.get_export_job(session, job_id, user)
    return payload


@router.get("/export/jobs/{job_id}/download", summary="Скачать результат выгрузки")
async def download_export(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> Response:
    _payload, file_object = await export_service.get_export_job(session, job_id, user)
    return await deliver_file(file_object)


# --- справочники админки (разовая предзагрузка, JSON/таблицы) ---------------------------


@router.get("/admin/dictionaries", summary="Список справочников", dependencies=[_ADMIN])
async def list_dictionaries(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    items = await dictionaries_service.list_dictionaries(session)
    return {"items": items, "total": len(items)}


@router.get(
    "/admin/dictionaries/{name}/items", summary="Элементы справочника", dependencies=[_ADMIN]
)
async def dictionary_items(
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    items, total = await dictionaries_service.dictionary_items(
        session, name, search=search, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/admin/dictionaries/{name}/import",
    summary="JSON-загрузка справочника",
)
async def import_dictionary(
    name: str,
    body: DictionaryImportBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    return await dictionaries_service.import_dictionary_json(
        session,
        name,
        items=body.items,
        update_strategy=body.update_strategy,
        user=user,
    )


@router.post(
    "/admin/dictionaries/{name}/items",
    status_code=201,
    summary="Добавить элемент справочника",
)
async def create_dictionary_item(
    name: str,
    body: DictionaryItemBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    return await dictionaries_service.create_item(
        session, name, body.model_dump(exclude_none=True), user
    )


@router.patch(
    "/admin/dictionaries/{name}/items/{item_id}",
    summary="Изменить элемент справочника",
)
async def update_dictionary_item(
    name: str,
    item_id: str,
    body: DictionaryItemBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, Any]:
    return await dictionaries_service.update_item(
        session, name, item_id, body.model_dump(exclude_unset=True), user
    )


@router.delete(
    "/admin/dictionaries/{name}/items/{item_id}",
    summary="Удалить (деактивировать) элемент справочника",
)
async def delete_dictionary_item(
    name: str,
    item_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, bool]:
    await dictionaries_service.delete_item(session, name, item_id, user)
    return {"ok": True}
