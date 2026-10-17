"""Справочник ИТ-направлений — каталог в БД наравне с вузами и ИТ-продуктами (требование ТЗ).

ИТ-направление — направление обучения в ИТ (DevOps, QA, анализ данных…). Программа ведётся
по одному направлению (`program.direction_id`), ИТ-продукт помогает учить по одному или
нескольким (`product_direction`, M:N).

Права — из каталога F2: читать справочник может любой пользователь CRM (направления нужны
в фильтрах отчётов, карточках программ и продуктов), создавать, править и деактивировать —
только с `admin:dictionaries`. Удаление мягкое (`is_active=false`): на направление
ссылаются программы, продукты и отчёты прошлых периодов.

Единое правило «направление записи» — `direction_match` / `direction_names`: им пользуются
реестр программ, дашборд, «Активность КАМов» и «Взаимодействия за период», поэтому
фильтр и колонка «ИТ-направление» везде отвечают одинаково:
- у программы записи задано направление — оно и есть направление записи;
- иначе — направления ИТ-продукта записи (продукт заявки или студента, без него —
  продукт программы); у продукта их может быть несколько.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.db import get_session
from app.core.errors import conflict, validation_error
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import get_current_user, require_permission
from app.models.crm import ItDirection, ProductDirection, Program
from app.models.user import AppUser
from app.modules.crm.deps import check_version, get_or_404
from app.modules.crm.schemas import (
    DirectionBrief,
    DirectionCreate,
    DirectionOut,
    DirectionUpdate,
)

router = APIRouter(prefix="/it-directions", tags=["it-directions"])

NOT_FOUND = "ИТ-направление не найдено"
MAX_DIRECTIONS_PER_ENTITY = 50

_SORT_FIELDS = {
    "sort_order": ItDirection.sort_order,
    "name": ItDirection.name,
    "code": ItDirection.code,
    "created_at": ItDirection.created_at,
    "updated_at": ItDirection.updated_at,
}


# --------------------------------------------------------------------------------------
# Правило «направление записи» (SQL) — общее для реестров и отчётов
# --------------------------------------------------------------------------------------


def _program_value(column: str, program_id: Any) -> Any:
    """`(SELECT program.<column> WHERE id = <program_id>)` — скалярный подзапрос.

    `correlate_except`: внешняя таблица записи (deal, student, activity, program)
    коррелируется на любой глубине вложенности, а не только с ближайшим SELECT."""
    prog = aliased(Program, name=f"dir_prog_{column}")
    return (
        select(getattr(prog, column))
        .where(prog.id == program_id)
        .correlate_except(prog)
        .scalar_subquery()
    )


def _record_product(program_id: Any, product_id: Any | None) -> Any:
    """Продукт записи: свой (заявка, студент), без него — продукт программы."""
    program_product = _program_value("product_id", program_id)
    return program_product if product_id is None else func.coalesce(product_id, program_product)


def direction_match(
    program_id: Any, product_id: Any | None, direction_ids: Iterable[uuid.UUID]
) -> ColumnElement[bool]:
    """Запись с программой `program_id` и продуктом `product_id` относится к одному из
    направлений `direction_ids`. Только скалярные подзапросы и EXISTS — строки записи не
    размножаются (DISTINCT не нужен), условие ставится в любой WHERE.

    `product_id=None` — у записи нет своего продукта (мероприятие): берётся продукт
    программы. Пустой список направлений вызывающий не передаёт (фильтр не задан).
    """
    ids = tuple(direction_ids)
    program_direction = _program_value("direction_id", program_id)
    link = aliased(ProductDirection, name="dir_link")
    by_product = (
        select(link.product_id)
        .where(
            link.product_id == _record_product(program_id, product_id),
            link.direction_id.in_(ids),
        )
        .correlate_except(link)
        .exists()
    )
    return sa.or_(
        program_direction.in_(ids),
        sa.and_(program_direction.is_(None), by_product),
    )


def direction_names(program_id: Any, product_id: Any | None) -> ColumnElement[Any]:
    """Подпись «ИТ-направление» записи по тому же правилу, что `direction_match`:
    направление программы или направления продукта через запятую (по `sort_order`)."""
    prog = aliased(Program, name="dir_name_prog")
    own = aliased(ItDirection, name="dir_name_own")
    by_program = (
        select(own.name)
        .select_from(prog)
        .join(own, own.id == prog.direction_id)
        .where(prog.id == program_id)
        .correlate_except(prog, own)
        .scalar_subquery()
    )
    link = aliased(ProductDirection, name="dir_name_link")
    linked = aliased(ItDirection, name="dir_name_product")
    by_product = (
        select(
            # string_agg(name, ', ' ORDER BY sort_order, name): ORDER BY — у второго аргумента
            func.string_agg(
                linked.name,
                aggregate_order_by(sa.literal_column("', '"), linked.sort_order, linked.name),
            )
        )
        .select_from(link)
        .join(linked, linked.id == link.direction_id)
        .where(link.product_id == _record_product(program_id, product_id))
        .correlate_except(link, linked)
        .scalar_subquery()
    )
    return func.coalesce(by_program, by_product)


# --------------------------------------------------------------------------------------
# Помощники для программ, продуктов и импорта
# --------------------------------------------------------------------------------------


def brief(direction: ItDirection) -> DirectionBrief:
    return DirectionBrief(
        id=direction.id, code=direction.code, name=direction.name, is_active=direction.is_active
    )


async def briefs_by_id(
    session: AsyncSession, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, DirectionBrief]:
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    rows = (
        (await session.execute(select(ItDirection).where(ItDirection.id.in_(wanted))))
        .scalars()
        .all()
    )
    return {row.id: brief(row) for row in rows}


async def direction_refs(session: AsyncSession, ids: Iterable[uuid.UUID]) -> list[dict[str, Any]]:
    """Выбранные в фильтре отчёта направления — для подписи фильтров в UI и файлах
    (`[{id, code, name}]` по `sort_order`); неизвестные id пропускаются."""
    wanted = set(ids)
    if not wanted:
        return []
    rows = (
        (
            await session.execute(
                select(ItDirection)
                .where(ItDirection.id.in_(wanted))
                .order_by(ItDirection.sort_order, ItDirection.name)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": str(row.id), "code": row.code, "name": row.name} for row in rows]


async def product_directions(
    session: AsyncSession, product_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, list[DirectionBrief]]:
    """Направления продуктов одним запросом: `{product_id: [направление, …]}` по
    `sort_order` и названию."""
    wanted = {item for item in product_ids if item is not None}
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(ProductDirection.product_id, ItDirection)
            .join(ItDirection, ItDirection.id == ProductDirection.direction_id)
            .where(ProductDirection.product_id.in_(wanted))
            .order_by(ItDirection.sort_order, ItDirection.name)
        )
    ).all()
    result: dict[uuid.UUID, list[DirectionBrief]] = {}
    for product_id, direction in rows:
        result.setdefault(product_id, []).append(brief(direction))
    return result


async def ensure_directions(
    session: AsyncSession,
    ids: Sequence[uuid.UUID],
    *,
    field: str,
    allow_inactive: Iterable[uuid.UUID] = (),
) -> list[uuid.UUID]:
    """Проверка ссылок на направления: все существуют, новые — активны (400 validation_error).

    `allow_inactive` — уже привязанные направления: деактивация справочника не мешает
    сохранить карточку, но выбрать деактивированное направление заново нельзя.
    """
    unique = list(dict.fromkeys(ids))
    if len(unique) > MAX_DIRECTIONS_PER_ENTITY:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": field,
                    "code": "too_many_values",
                    "message": f"Не больше {MAX_DIRECTIONS_PER_ENTITY} ИТ-направлений",
                }
            ],
        )
    if not unique:
        return []
    found = {
        row.id: row
        for row in (
            await session.execute(select(ItDirection).where(ItDirection.id.in_(unique)))
        ).scalars()
    }
    keep = set(allow_inactive)
    details = []
    for item in unique:
        direction = found.get(item)
        if direction is None:
            details.append(
                {"field": field, "code": "lookup_not_found", "message": f"{NOT_FOUND}: {item}"}
            )
        elif not direction.is_active and item not in keep:
            details.append(
                {
                    "field": field,
                    "code": "inactive",
                    "message": f"ИТ-направление «{direction.name}» деактивировано",
                }
            )
    if details:
        raise validation_error("Ошибка валидации данных", details=details)
    return unique


async def set_product_directions(
    session: AsyncSession, product_id: uuid.UUID, direction_ids: Sequence[uuid.UUID]
) -> None:
    """Заменяет набор направлений продукта (связи без изменений не трогаются)."""
    wanted = set(direction_ids)
    current = set(
        (
            await session.execute(
                select(ProductDirection.direction_id).where(
                    ProductDirection.product_id == product_id
                )
            )
        ).scalars()
    )
    removed = current - wanted
    if removed:
        await session.execute(
            sa.delete(ProductDirection).where(
                ProductDirection.product_id == product_id,
                ProductDirection.direction_id.in_(removed),
            )
        )
    for direction_id in sorted(wanted - current, key=str):
        session.add(ProductDirection(product_id=product_id, direction_id=direction_id))
    await session.flush()


async def link_product_directions(
    session: AsyncSession, product_id: uuid.UUID, direction_ids: Iterable[uuid.UUID]
) -> int:
    """Добавляет продукту направления, не снимая прежних (импорт: «продукт по
    направлению» из строки файла). Возвращает число новых связей."""
    wanted = set(direction_ids)
    if not wanted:
        return 0
    current = set(
        (
            await session.execute(
                select(ProductDirection.direction_id).where(
                    ProductDirection.product_id == product_id
                )
            )
        ).scalars()
    )
    added = sorted(wanted - current, key=str)
    for direction_id in added:
        session.add(ProductDirection(product_id=product_id, direction_id=direction_id))
    if added:
        await session.flush()
    return len(added)


async def load_direction_lookup(session: AsyncSession) -> dict[str, ItDirection]:
    """Поиск направления по коду или названию без регистра (импорт: «DevOps», «devops»)."""
    rows = (await session.execute(select(ItDirection))).scalars().all()
    lookup: dict[str, ItDirection] = {}
    for row in rows:
        lookup.setdefault(row.name.strip().lower(), row)
    for row in rows:
        lookup[row.code.strip().lower()] = row  # код — точнее названия
    return lookup


# --------------------------------------------------------------------------------------
# CRUD справочника
# --------------------------------------------------------------------------------------


async def _ensure_unique(
    session: AsyncSession,
    *,
    code: str | None = None,
    name: str | None = None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    for column, value, message in (
        (ItDirection.code, code, "ИТ-направление с таким кодом уже существует"),
        (ItDirection.name, name, "ИТ-направление с таким названием уже существует"),
    ):
        if value is None:
            continue
        stmt = select(ItDirection.id).where(func.lower(column) == value.strip().lower())
        if exclude_id is not None:
            stmt = stmt.where(ItDirection.id != exclude_id)
        if (await session.execute(stmt)).first() is not None:
            raise conflict(message)


async def _usage_counts(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    if not ids:
        return {}, {}
    programs = dict(
        (
            await session.execute(
                select(Program.direction_id, func.count())
                .where(Program.direction_id.in_(ids), Program.is_active.is_(True))
                .group_by(Program.direction_id)
            )
        ).all()
    )
    products = dict(
        (
            await session.execute(
                select(ProductDirection.direction_id, func.count())
                .where(ProductDirection.direction_id.in_(ids))
                .group_by(ProductDirection.direction_id)
            )
        ).all()
    )
    return programs, products


async def _to_out(session: AsyncSession, direction: ItDirection) -> DirectionOut:
    programs, products = await _usage_counts(session, [direction.id])
    out = DirectionOut.model_validate(direction)
    out.programs_count = int(programs.get(direction.id, 0))
    out.products_count = int(products.get(direction.id, 0))
    return out


async def _ensure_can_deactivate(session: AsyncSession, direction: ItDirection) -> None:
    """Программа ведётся по одному направлению: деактивировать направление активных
    программ нельзя — сначала переназначьте программы (как с продуктом и программами).
    Связи с продуктами (M:N) деактивации не мешают и сохраняются для отчётов."""
    linked = (
        await session.execute(
            select(func.count()).where(
                Program.direction_id == direction.id, Program.is_active.is_(True)
            )
        )
    ).scalar_one()
    if linked:
        raise conflict(
            f"Нельзя деактивировать направление «{direction.name}»: по нему ведётся "
            f"{linked} активных программ — сначала смените им направление"
        )


@router.get("", summary="Справочник ИТ-направлений")
async def list_directions(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
    params: Annotated[ListParams, Depends(list_params)],
    search: Annotated[str | None, Query(description="Поиск по названию и коду")] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(ItDirection)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(sa.or_(ItDirection.name.ilike(pattern), ItDirection.code.ilike(pattern)))
    if is_active is not None:
        stmt = stmt.where(ItDirection.is_active.is_(is_active))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="sort_order,name")
    rows = (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    programs, products = await _usage_counts(session, [row.id for row in rows])
    items = []
    for row in rows:
        out = DirectionOut.model_validate(row)
        out.programs_count = int(programs.get(row.id, 0))
        out.products_count = int(products.get(row.id, 0))
        items.append(out.model_dump(mode="json"))
    return list_envelope(items, total, params)


@router.post("", status_code=201, summary="Создать ИТ-направление")
async def create_direction(
    body: DirectionCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("admin:dictionaries"))],
) -> DirectionOut:
    payload = body.model_dump()
    payload["code"] = payload["code"].strip()
    payload["name"] = payload["name"].strip()
    await _ensure_unique(session, code=payload["code"], name=payload["name"])
    direction = ItDirection(**payload)
    session.add(direction)
    await session.commit()
    return await _to_out(session, direction)


@router.get("/{direction_id}", summary="Карточка ИТ-направления")
async def get_direction(
    direction_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> DirectionOut:
    direction = await get_or_404(session, ItDirection, direction_id, NOT_FOUND)
    return await _to_out(session, direction)


@router.patch("/{direction_id}", summary="Изменить ИТ-направление")
async def update_direction(
    direction_id: uuid.UUID,
    body: DirectionUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("admin:dictionaries"))],
) -> DirectionOut:
    direction = await get_or_404(session, ItDirection, direction_id, NOT_FOUND)
    check_version(direction, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version"})
    for key in ("code", "name"):
        if key in payload:
            if payload[key] is None:
                raise validation_error(
                    "Ошибка валидации данных",
                    details=[{"field": key, "code": "required", "message": "Поле обязательно"}],
                )
            payload[key] = payload[key].strip()
    if "sort_order" in payload and payload["sort_order"] is None:
        payload.pop("sort_order")
    if "is_active" in payload and payload["is_active"] is None:
        payload.pop("is_active")
    await _ensure_unique(
        session, code=payload.get("code"), name=payload.get("name"), exclude_id=direction_id
    )
    if payload.get("is_active") is False and direction.is_active:
        await _ensure_can_deactivate(session, direction)
    for attr, value in payload.items():
        setattr(direction, attr, value)
    await session.commit()
    return await _to_out(session, direction)


@router.delete("/{direction_id}", summary="Деактивировать ИТ-направление")
async def delete_direction(
    direction_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_permission("admin:dictionaries"))],
) -> dict[str, bool]:
    direction = await get_or_404(session, ItDirection, direction_id, NOT_FOUND)
    if direction.is_active:
        await _ensure_can_deactivate(session, direction)
        direction.is_active = False
        await session.commit()
    return {"ok": True}
