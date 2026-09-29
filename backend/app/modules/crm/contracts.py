"""Договоры «1 договор — N продуктов» (api-contract.md §3.2, data-model.md §4.6)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import client_ip, write_audit
from app.core.config import get_settings as get_app_settings
from app.core.db import get_session
from app.core.errors import conflict, not_found, validation_error
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import get_current_user, require_roles
from app.models.crm import Contract, ContractProduct, Counterparty, Product, University
from app.models.service import FileObject
from app.models.user import AppUser
from app.modules.crm.deps import check_version, ensure_can_write_owned, get_or_404
from app.modules.crm.schemas import ContractCreate, ContractOut, ContractUpdate, FileOut
from app.modules.files.service import soft_delete_file, store_upload

router = APIRouter(prefix="/contracts", tags=["contracts"])

_SORT_FIELDS = {
    "number": Contract.number,
    "status": Contract.status,
    "valid_from": Contract.valid_from,
    "valid_to": Contract.valid_to,
    "amount": Contract.amount,
    "created_at": Contract.created_at,
    "updated_at": Contract.updated_at,
}


def _to_out(contract: Contract, warning: str | None = None) -> ContractOut:
    out = ContractOut.model_validate(contract)
    out.product_ids = [link.product_id for link in contract.products]
    out.warning_duplicate = warning
    return out


async def _load_contract(session: AsyncSession, contract_id: uuid.UUID) -> Contract:
    contract = (
        await session.execute(
            select(Contract)
            .options(selectinload(Contract.products))
            .where(Contract.id == contract_id)
        )
    ).scalar_one_or_none()
    if contract is None:
        raise not_found("Договор не найден")
    return contract


async def _ensure_unique_number(
    session: AsyncSession, number: str, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(Contract.id).where(func.lower(Contract.number) == number.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(Contract.id != exclude_id)
    if (await session.execute(stmt)).first() is not None:
        raise conflict("Договор с таким номером уже существует")


async def _validate_products(session: AsyncSession, product_ids: list[uuid.UUID]) -> None:
    if not product_ids:
        return
    found = (
        (await session.execute(select(Product.id).where(Product.id.in_(product_ids))))
        .scalars()
        .all()
    )
    missing = set(product_ids) - set(found)
    if missing:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "product_ids",
                    "code": "lookup_not_found",
                    "message": f"Продукты не найдены: {', '.join(str(m) for m in missing)}",
                }
            ],
        )


def _sync_products(
    contract: Contract, product_ids: list[uuid.UUID], actor_id: uuid.UUID
) -> None:
    """Расширение набора = добавление строк contract_product (added_by/added_at — история)."""
    existing = {link.product_id: link for link in contract.products}
    target = set(product_ids)
    for product_id in target - set(existing):
        contract.products.append(
            ContractProduct(product_id=product_id, added_by=actor_id)
        )
    for product_id, link in existing.items():
        if product_id not in target:
            contract.products.remove(link)


@router.get("", summary="Реестр договоров")
async def list_contracts(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
    params: Annotated[ListParams, Depends(list_params)],
    university_id: Annotated[uuid.UUID | None, Query()] = None,
    counterparty_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query(description="Номер договора")] = None,
) -> dict[str, Any]:
    stmt = select(Contract).options(selectinload(Contract.products))
    if university_id:
        stmt = stmt.where(Contract.university_id == university_id)
    if counterparty_id:
        stmt = stmt.where(Contract.counterparty_id == counterparty_id)
    if status:
        stmt = stmt.where(Contract.status == status)
    if search:
        stmt = stmt.where(Contract.number.ilike(f"%{search.strip()}%"))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="-created_at")
    rows = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    )
    items = [_to_out(row).model_dump(mode="json", exclude={"files"}) for row in rows]
    return list_envelope(items, total, params)


@router.post("", status_code=201, summary="Создать договор")
async def create_contract(
    body: ContractCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> ContractOut:
    await _ensure_unique_number(session, body.number)
    if body.university_id is not None:
        await get_or_404(session, University, body.university_id, "Вуз не найден")
    if body.counterparty_id is not None:
        await get_or_404(session, Counterparty, body.counterparty_id, "Контрагент не найден")
    await _validate_products(session, body.product_ids)

    warning: str | None = None
    if body.university_id is not None:
        active_count = (
            await session.execute(
                select(func.count()).where(
                    Contract.university_id == body.university_id,
                    Contract.status == "active",
                )
            )
        ).scalar_one()
        if active_count:
            # решение кейсодержателя: «как правило один» — предупреждение, не запрет
            warning = "У вуза уже есть активный договор — проверьте, не дубль ли это"

    contract = Contract(**body.model_dump(exclude={"product_ids"}))
    for product_id in body.product_ids:
        contract.products.append(ContractProduct(product_id=product_id, added_by=user.id))
    session.add(contract)
    await session.flush()
    await write_audit(
        session,
        actor_user_id=user.id,
        action="contract.created",
        entity_type="contract",
        entity_id=contract.id,
        after={"number": contract.number, "status": contract.status},
        ip=client_ip(request),
    )
    await session.commit()
    return _to_out(contract, warning)


@router.get("/{contract_id}", summary="Карточка договора со списком файлов")
async def get_contract(
    contract_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> ContractOut:
    contract = await _load_contract(session, contract_id)
    files = (
        (
            await session.execute(
                select(FileObject)
                .where(
                    FileObject.entity_type == "contract",
                    FileObject.entity_id == contract_id,
                    FileObject.deleted_at.is_(None),
                )
                .order_by(FileObject.created_at)
            )
        )
        .scalars()
        .all()
    )
    out = _to_out(contract)
    out.files = [FileOut.model_validate(f) for f in files]
    return out


@router.patch("/{contract_id}", summary="Изменить договор")
async def update_contract(
    contract_id: uuid.UUID,
    body: ContractUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> ContractOut:
    contract = await _load_contract(session, contract_id)
    ensure_can_write_owned(user, contract.responsible_user_id)
    check_version(contract, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version", "product_ids"})
    if "number" in payload:
        await _ensure_unique_number(session, payload["number"], exclude_id=contract_id)
    for attr, value in payload.items():
        setattr(contract, attr, value)
    if body.product_ids is not None:
        await _validate_products(session, body.product_ids)
        _sync_products(contract, body.product_ids, user.id)
    await session.commit()
    return _to_out(contract)


@router.delete("/{contract_id}", summary="Расторгнуть (мягко удалить) договор")
async def delete_contract(
    contract_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
    request: Request,
) -> dict[str, bool]:
    # DDL contract не содержит deleted_at (data-model.md §4.6 — высший приоритет),
    # поэтому «мягкое удаление» из api-contract реализовано терминальным статусом
    contract = await _load_contract(session, contract_id)
    before = contract.status
    contract.status = "terminated"
    await write_audit(
        session,
        actor_user_id=user.id,
        action="contract.terminated",
        entity_type="contract",
        entity_id=contract.id,
        before={"status": before},
        after={"status": "terminated"},
        ip=client_ip(request),
    )
    await session.commit()
    return {"ok": True}


# --- файлы договора (MinIO, bucket crm-files) ---------------------------------------------------


@router.get("/{contract_id}/files", summary="Файлы договора")
async def list_contract_files(
    contract_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> list[FileOut]:
    await _load_contract(session, contract_id)
    files = (
        (
            await session.execute(
                select(FileObject)
                .where(
                    FileObject.entity_type == "contract",
                    FileObject.entity_id == contract_id,
                    FileObject.deleted_at.is_(None),
                )
                .order_by(FileObject.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [FileOut.model_validate(f) for f in files]


@router.post(
    "/{contract_id}/files",
    status_code=201,
    summary="Загрузить скан договора",
)
async def upload_contract_file(
    contract_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="Скан договора (до 50 МБ)")],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> FileOut:
    contract = await _load_contract(session, contract_id)
    ensure_can_write_owned(user, contract.responsible_user_id)
    file_object = await store_upload(
        session,
        upload=file,
        bucket=get_app_settings().s3_bucket_files,
        entity_type="contract",
        entity_id=contract_id,
        uploaded_by=user.id,
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        action="contract.file_uploaded",
        entity_type="contract",
        entity_id=contract_id,
        after={"file_id": str(file_object.id), "file_name": file_object.original_name},
        ip=client_ip(request),
    )
    await session.commit()
    return FileOut.model_validate(file_object)


@router.delete(
    "/{contract_id}/files/{file_id}",
    summary="Удалить файл договора",
    dependencies=[Depends(require_roles("admin", "head_kam"))],
)
async def delete_contract_file(
    contract_id: uuid.UUID,
    file_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    await _load_contract(session, contract_id)
    file_object = await get_or_404(session, FileObject, file_id, "Файл не найден")
    if file_object.entity_type != "contract" or file_object.entity_id != contract_id:
        raise not_found("Файл не найден")
    soft_delete_file(file_object)  # объект в MinIO чистит POST /internal/jobs/purge-deleted
    await session.commit()
    return {"ok": True}
