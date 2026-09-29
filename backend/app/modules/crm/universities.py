"""Вузы и их контактные лица: полный CRUD (api-contract.md §3.1, RBAC §12.1)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.cache import get_cache
from app.core.db import get_session
from app.core.errors import conflict, not_found
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import get_current_user, require_roles
from app.models.crm import Contract, University, UniversityContact
from app.models.deal import Deal
from app.models.talent import Student
from app.models.user import AppUser
from app.modules.crm.deps import check_version, ensure_can_write_owned, get_or_404
from app.modules.crm.schemas import (
    ContactCreate,
    ContactOut,
    ContactUpdate,
    UniversityAggregates,
    UniversityCreate,
    UniversityOut,
    UniversityUpdate,
)

router = APIRouter(prefix="/universities", tags=["universities"])

_SORT_FIELDS = {
    "name": University.name,
    "region": University.region,
    "city": University.city,
    "created_at": University.created_at,
    "updated_at": University.updated_at,
}


async def _ensure_unique_name(
    session: AsyncSession, name: str, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(University.id).where(func.lower(University.name) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(University.id != exclude_id)
    if (await session.execute(stmt)).first() is not None:
        raise conflict("Вуз с таким названием уже существует")


async def _invalidate_dict_cache() -> None:
    await get_cache().delete("dict:universities")


@router.get("", summary="Реестр вузов")
async def list_universities(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
    params: Annotated[ListParams, Depends(list_params)],
    search: Annotated[str | None, Query(description="Название / ИНН / город")] = None,
    region: Annotated[str | None, Query()] = None,
    kam_user_id: Annotated[uuid.UUID | None, Query()] = None,
    has_active_contract: Annotated[bool | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(University)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            sa.or_(
                University.name.ilike(pattern),
                University.short_name.ilike(pattern),
                University.inn.ilike(f"{search.strip()}%"),
                University.city.ilike(pattern),
            )
        )
    if region:
        stmt = stmt.where(University.region == region)
    if kam_user_id:
        stmt = stmt.where(University.kam_user_id == kam_user_id)
    if is_active is not None:
        stmt = stmt.where(University.is_active.is_(is_active))
    if has_active_contract is not None:
        active_contract = (
            select(Contract.id)
            .where(Contract.university_id == University.id, Contract.status == "active")
            .exists()
        )
        stmt = stmt.where(active_contract if has_active_contract else ~active_contract)

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="name")
    rows = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    )
    items = [UniversityOut.model_validate(row).model_dump(mode="json") for row in rows]
    return list_envelope(items, total, params)


@router.post(
    "",
    status_code=201,
    summary="Создать вуз",
    dependencies=[Depends(require_roles("admin", "head_kam", "kam"))],
)
async def create_university(
    body: UniversityCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
    request: Request,
) -> UniversityOut:
    await _ensure_unique_name(session, body.name)
    university = University(**body.model_dump())
    session.add(university)
    await session.flush()
    await write_audit(
        session,
        actor_user_id=user.id,
        action="university.created",
        entity_type="university",
        entity_id=university.id,
        after={"name": university.name},
        ip=client_ip(request),
    )
    await session.commit()
    await _invalidate_dict_cache()
    return UniversityOut.model_validate(university)


@router.get("/{university_id}", summary="Карточка вуза с агрегатами")
async def get_university(
    university_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> UniversityOut:
    university = await get_or_404(session, University, university_id, "Вуз не найден")
    contracts_count = (
        await session.execute(
            select(func.count()).where(Contract.university_id == university_id)
        )
    ).scalar_one()
    active_requests = (
        await session.execute(
            select(func.count()).where(
                Deal.university_id == university_id, Deal.status == "open"
            )
        )
    ).scalar_one()
    students_count = (
        await session.execute(
            select(func.count()).where(Student.university_id == university_id)
        )
    ).scalar_one()
    out = UniversityOut.model_validate(university)
    out.aggregates = UniversityAggregates(
        contracts_count=contracts_count,
        active_requests=active_requests,
        students_count=students_count,
    )
    return out


@router.patch("/{university_id}", summary="Изменить вуз")
async def update_university(
    university_id: uuid.UUID,
    body: UniversityUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> UniversityOut:
    university = await get_or_404(session, University, university_id, "Вуз не найден")
    ensure_can_write_owned(user, university.kam_user_id)
    check_version(university, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version"})
    if "name" in payload:
        await _ensure_unique_name(session, payload["name"], exclude_id=university_id)
    for attr, value in payload.items():
        setattr(university, attr, value)
    await session.commit()
    await _invalidate_dict_cache()
    return UniversityOut.model_validate(university)


@router.delete("/{university_id}", summary="Деактивировать вуз")
async def delete_university(
    university_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
    request: Request,
) -> dict[str, bool]:
    university = await get_or_404(session, University, university_id, "Вуз не найден")
    open_deals = (
        await session.execute(
            select(func.count()).where(
                Deal.university_id == university_id, Deal.status == "open"
            )
        )
    ).scalar_one()
    if open_deals:
        raise conflict(
            f"Нельзя деактивировать вуз: по нему {open_deals} активных заявок"
        )
    university.is_active = False
    await write_audit(
        session,
        actor_user_id=user.id,
        action="university.deactivated",
        entity_type="university",
        entity_id=university.id,
        before={"is_active": True},
        after={"is_active": False},
        ip=client_ip(request),
    )
    await session.commit()
    await _invalidate_dict_cache()
    return {"ok": True}


# --- контактные лица --------------------------------------------------------------------------


@router.get("/{university_id}/contacts", summary="Контактные лица вуза")
async def list_contacts(
    university_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> list[ContactOut]:
    await get_or_404(session, University, university_id, "Вуз не найден")
    rows = (
        (
            await session.execute(
                select(UniversityContact)
                .where(UniversityContact.university_id == university_id)
                .order_by(UniversityContact.is_primary.desc(), UniversityContact.full_name)
            )
        )
        .scalars()
        .all()
    )
    return [ContactOut.model_validate(row) for row in rows]


@router.post(
    "/{university_id}/contacts",
    status_code=201,
    summary="Добавить контактное лицо",
    dependencies=[Depends(require_roles("admin", "head_kam", "kam"))],
)
async def create_contact(
    university_id: uuid.UUID,
    body: ContactCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactOut:
    await get_or_404(session, University, university_id, "Вуз не найден")
    contact = UniversityContact(university_id=university_id, **body.model_dump())
    session.add(contact)
    await session.commit()
    return ContactOut.model_validate(contact)


@router.patch(
    "/{university_id}/contacts/{contact_id}",
    summary="Изменить контактное лицо",
    dependencies=[Depends(require_roles("admin", "head_kam", "kam"))],
)
async def update_contact(
    university_id: uuid.UUID,
    contact_id: uuid.UUID,
    body: ContactUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactOut:
    contact = await get_or_404(session, UniversityContact, contact_id, "Контакт не найден")
    if contact.university_id != university_id:
        raise not_found("Контакт не найден")
    check_version(contact, body.version)
    for attr, value in body.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(contact, attr, value)
    await session.commit()
    return ContactOut.model_validate(contact)


@router.delete(
    "/{university_id}/contacts/{contact_id}",
    summary="Удалить контактное лицо",
    dependencies=[Depends(require_roles("admin", "head_kam"))],
)
async def delete_contact(
    university_id: uuid.UUID,
    contact_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    contact = await get_or_404(session, UniversityContact, contact_id, "Контакт не найден")
    if contact.university_id != university_id:
        raise not_found("Контакт не найден")
    await session.delete(contact)
    await session.commit()
    return {"ok": True}
