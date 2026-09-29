"""Продукты каталога (api-contract.md §3.3)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.core.db import get_session
from app.core.errors import conflict
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import get_current_user, require_roles
from app.models.crm import Product, Program
from app.models.user import AppUser
from app.modules.crm.deps import check_version, get_or_404
from app.modules.crm.schemas import ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])

_SORT_FIELDS = {
    "name": Product.name,
    "code": Product.code,
    "created_at": Product.created_at,
    "updated_at": Product.updated_at,
}


async def _ensure_unique_code(
    session: AsyncSession, code: str, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(Product.id).where(func.lower(Product.code) == code.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    if (await session.execute(stmt)).first() is not None:
        raise conflict("Продукт с таким кодом уже существует")


@router.get("", summary="Каталог продуктов")
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
    params: Annotated[ListParams, Depends(list_params)],
    search: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Product)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(sa.or_(Product.name.ilike(pattern), Product.code.ilike(pattern)))
    if is_active is not None:
        stmt = stmt.where(Product.is_active.is_(is_active))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="name")
    rows = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    )
    items = [ProductOut.model_validate(row).model_dump(mode="json") for row in rows]
    return list_envelope(items, total, params)


@router.post("", status_code=201, summary="Создать продукт")
async def create_product(
    body: ProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_roles("admin", "head_kam"))],
) -> ProductOut:
    await _ensure_unique_code(session, body.code)
    product = Product(**body.model_dump())
    session.add(product)
    await session.commit()
    await get_cache().delete("dict:products")
    return ProductOut.model_validate(product)


@router.get("/{product_id}", summary="Карточка продукта")
async def get_product(
    product_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> ProductOut:
    product = await get_or_404(session, Product, product_id, "Продукт не найден")
    return ProductOut.model_validate(product)


@router.patch("/{product_id}", summary="Изменить продукт")
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_roles("admin", "head_kam"))],
) -> ProductOut:
    product = await get_or_404(session, Product, product_id, "Продукт не найден")
    check_version(product, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version"})
    if "code" in payload:
        await _ensure_unique_code(session, payload["code"], exclude_id=product_id)
    for attr, value in payload.items():
        setattr(product, attr, value)
    await session.commit()
    await get_cache().delete("dict:products")
    return ProductOut.model_validate(product)


@router.delete("/{product_id}", summary="Деактивировать продукт")
async def delete_product(
    product_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, bool]:
    product = await get_or_404(session, Product, product_id, "Продукт не найден")
    linked_programs = (
        await session.execute(
            select(func.count()).where(
                Program.product_id == product_id, Program.is_active.is_(True)
            )
        )
    ).scalar_one()
    if linked_programs:
        raise conflict(
            f"Нельзя удалить продукт: к нему привязано {linked_programs} активных программ"
        )
    product.is_active = False
    await session.commit()
    await get_cache().delete("dict:products")
    return {"ok": True}
