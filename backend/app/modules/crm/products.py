"""Продукты каталога (api-contract.md §3.3).

ИТ-продукт помогает учить по одному или нескольким ИТ-направлениям (`product_direction`,
M:N): `direction_ids` в теле создания и правки (передан — набор заменяется целиком),
`direction_ids` + `directions` в ответе, фильтр списка `direction_id` (повторяемый).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.core.db import get_session
from app.core.errors import conflict
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import require_permission
from app.models.crm import Product, ProductDirection, Program
from app.models.user import AppUser
from app.modules.crm import directions as directions_service
from app.modules.crm.deps import check_version, get_or_404
from app.modules.crm.schemas import DirectionBrief, ProductCreate, ProductOut, ProductUpdate

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


def _to_out(product: Product, directions: list[DirectionBrief] | None = None) -> ProductOut:
    out = ProductOut.model_validate(product)
    out.directions = list(directions or [])
    out.direction_ids = [direction.id for direction in out.directions]
    return out


async def _out(session: AsyncSession, product: Product) -> ProductOut:
    linked = await directions_service.product_directions(session, [product.id])
    return _to_out(product, linked.get(product.id))


@router.get("", summary="Каталог продуктов")
async def list_products(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("products:read"))],
    params: Annotated[ListParams, Depends(list_params)],
    search: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    direction_id: Annotated[
        list[uuid.UUID] | None,
        Query(description="ИТ-направление — повторяемый параметр, продукт с любым из них"),
    ] = None,
) -> dict[str, Any]:
    stmt = select(Product)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(sa.or_(Product.name.ilike(pattern), Product.code.ilike(pattern)))
    if is_active is not None:
        stmt = stmt.where(Product.is_active.is_(is_active))
    if direction_id:
        stmt = stmt.where(
            Product.id.in_(
                select(ProductDirection.product_id).where(
                    ProductDirection.direction_id.in_(direction_id)
                )
            )
        )
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="name")
    rows = (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    linked = await directions_service.product_directions(session, [row.id for row in rows])
    items = [_to_out(row, linked.get(row.id)).model_dump(mode="json") for row in rows]
    return list_envelope(items, total, params)


@router.post("", status_code=201, summary="Создать продукт")
async def create_product(
    body: ProductCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("products:write"))],
) -> ProductOut:
    await _ensure_unique_code(session, body.code)
    direction_ids = await directions_service.ensure_directions(
        session, body.direction_ids, field="direction_ids"
    )
    product = Product(**body.model_dump(exclude={"direction_ids"}))
    session.add(product)
    await session.flush()
    await directions_service.set_product_directions(session, product.id, direction_ids)
    await session.commit()
    await get_cache().delete("dict:products")
    return await _out(session, product)


@router.get("/{product_id}", summary="Карточка продукта")
async def get_product(
    product_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("products:read"))],
) -> ProductOut:
    product = await get_or_404(session, Product, product_id, "Продукт не найден")
    return await _out(session, product)


@router.patch("/{product_id}", summary="Изменить продукт")
async def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("products:write"))],
) -> ProductOut:
    product = await get_or_404(session, Product, product_id, "Продукт не найден")
    check_version(product, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version"})
    direction_ids = payload.pop("direction_ids", None)
    if "code" in payload:
        await _ensure_unique_code(session, payload["code"], exclude_id=product_id)
    if direction_ids is not None:
        current = await directions_service.product_directions(session, [product_id])
        direction_ids = await directions_service.ensure_directions(
            session,
            direction_ids,
            field="direction_ids",
            allow_inactive=[d.id for d in current.get(product_id, [])],
        )
    for attr, value in payload.items():
        setattr(product, attr, value)
    if direction_ids is not None:
        before = {d.id for d in current.get(product_id, [])}
        if set(direction_ids) != before:
            # смена набора направлений — правка карточки: новая версия и updated_at
            product.updated_at = datetime.now(timezone.utc)
        await directions_service.set_product_directions(session, product_id, direction_ids)
    await session.commit()
    await get_cache().delete("dict:products")
    return await _out(session, product)


@router.delete("/{product_id}", summary="Деактивировать продукт")
async def delete_product(
    product_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("products:delete"))],
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
