"""Справочники админки: разовая предзагрузка через диалог (api-contract.md §10.1).

Два пути (оба только admin): JSON напрямую (`POST /admin/dictionaries/{name}/import`)
и общий конвейер импорта §6 с `entity_type=dictionary:<name>`.

Резолюция (data-model приоритетнее api-contract): универсальной таблицы справочников
в DDL нет, поэтому табличные справочники (`federal_projects`, `interaction_types`,
`universities_registry`, `products`) живут в своих таблицах, а справочники-списки значений
(`regions`, `request_sources`, `activity_kinds`, `tags`) — в `app_setting['dict:{name}']`
(jsonb, admin-only запись).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, not_found
from app.core.pii import get_pii_crypto
from app.models.crm import InteractionType, Product, University
from app.models.service import AppSetting
from app.models.talent import FederalProject
from app.models.user import AppUser
from app.modules.import_export.importers import (
    PreparedRow,
    get_importer,
    prepare_context,
)
from app.modules.import_export.mapping import VALUE_LIST_DICTIONARIES, entity_fields

DICTIONARIES: dict[str, str] = {
    "regions": "Регионы",
    "federal_projects": "Федеральные проекты",
    "universities_registry": "Реестр вузов РФ (предзагрузка)",
    "interaction_types": "Типы взаимодействия B2C",
    "products": "Продукты (вендоры)",
    "request_sources": "Источники заявок",
    "activity_kinds": "Виды активностей",
    "tags": "Теги",
}


def _ensure_known(name: str) -> None:
    if name not in DICTIONARIES:
        raise not_found(
            f"Справочник «{name}» не найден. Доступно: {', '.join(sorted(DICTIONARIES))}"
        )


def _is_value_list(name: str) -> bool:
    return name in VALUE_LIST_DICTIONARIES


async def _value_list_setting(session: AsyncSession, name: str) -> AppSetting:
    key = f"dict:{name}"
    setting = await session.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value={"items": []})
        session.add(setting)
        await session.flush()
    return setting


# --------------------------------------------------------------------------------------
# Списки
# --------------------------------------------------------------------------------------


async def list_dictionaries(session: AsyncSession) -> list[dict[str, Any]]:
    result = []
    for name, label in DICTIONARIES.items():
        if name == "federal_projects":
            count = (
                await session.execute(select(func.count()).select_from(FederalProject))
            ).scalar_one()
        elif name == "interaction_types":
            count = (
                await session.execute(select(func.count()).select_from(InteractionType))
            ).scalar_one()
        elif name == "universities_registry":
            count = (
                await session.execute(select(func.count()).select_from(University))
            ).scalar_one()
        elif name == "products":
            count = (
                await session.execute(select(func.count()).select_from(Product))
            ).scalar_one()
        else:
            setting = await session.get(AppSetting, f"dict:{name}")
            count = len((setting.value or {}).get("items") or []) if setting else 0
        result.append({"name": name, "label": label, "items_count": int(count)})
    return result


def _fed_project_item(row: FederalProject) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "code": row.code,
        "description": row.description,
        "starts_on": row.starts_on.isoformat() if row.starts_on else None,
        "ends_on": row.ends_on.isoformat() if row.ends_on else None,
        "is_active": row.is_active,
    }


def _interaction_type_item(row: InteractionType) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "is_active": row.is_active,
    }


def _product_item(row: Product) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "product_type": row.product_type,
        "description": row.description,
        "is_active": row.is_active,
    }


def _university_item(row: University) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "short_name": row.short_name,
        "inn": row.inn,
        "region": row.region,
        "city": row.city,
        "is_active": row.is_active,
    }


async def dictionary_items(
    session: AsyncSession,
    name: str,
    *,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    _ensure_known(name)
    if _is_value_list(name):
        setting = await session.get(AppSetting, f"dict:{name}")
        items = list((setting.value or {}).get("items") or []) if setting else []
        if search:
            needle = search.strip().lower()
            items = [i for i in items if needle in (i.get("name") or "").lower()]
        return items[offset : offset + limit], len(items)

    model_map = {
        "federal_projects": (FederalProject, FederalProject.name, _fed_project_item),
        "interaction_types": (InteractionType, InteractionType.name, _interaction_type_item),
        "universities_registry": (University, University.name, _university_item),
        "products": (Product, Product.name, _product_item),
    }
    model, name_column, serializer = model_map[name]
    stmt = select(model)
    if search:
        stmt = stmt.where(name_column.ilike(f"%{search.strip()}%"))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        (await session.execute(stmt.order_by(name_column).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return [serializer(row) for row in rows], int(total)


# --------------------------------------------------------------------------------------
# JSON-импорт: переиспользует построчные импортёры конвейера §6
# --------------------------------------------------------------------------------------


async def import_dictionary_json(
    session: AsyncSession,
    name: str,
    *,
    items: list[dict[str, Any]],
    update_strategy: str,
    user: AppUser,
) -> dict[str, Any]:
    _ensure_known(name)
    entity_type = f"dictionary:{name}"
    spec = entity_fields(entity_type)
    known_fields = set(spec.by_name())
    ctx = await prepare_context(
        session,
        entity_type=entity_type,
        entries=[],
        raw_options={"update_strategy": update_strategy},
        actor=user,
        crypto=get_pii_crypto(),
        import_session_id=uuid.uuid4(),  # JSON-путь без сессии импорта
    )
    rows = [
        PreparedRow(
            row_number=index + 1,
            values={
                key: (str(value) if value is not None else None)
                for key, value in item.items()
                if key in known_fields
            },
        )
        for index, item in enumerate(items)
    ]
    importer = get_importer(entity_type)
    await importer.validate(ctx, rows)
    valid_rows = [row for row in rows if row.valid]
    stats = await importer.apply(ctx, valid_rows)
    errors = [issue.as_dict() for row in rows for issue in row.issues]
    await session.commit()
    return {
        "created": stats.created,
        "updated": stats.updated,
        "failed": len(rows) - len(valid_rows),
        "errors": errors[:200],
    }


# --------------------------------------------------------------------------------------
# Точечный CRUD
# --------------------------------------------------------------------------------------


async def create_item(
    session: AsyncSession, name: str, payload: dict[str, Any], user: AppUser
) -> dict[str, Any]:
    result = await import_dictionary_json(
        session, name, items=[payload], update_strategy="create_only", user=user
    )
    if result["failed"]:
        message = "; ".join(e["message"] for e in result["errors"]) or "Элемент не создан"
        raise conflict(message)
    if not result["created"]:
        raise conflict("Такой элемент уже существует")
    return result


async def update_item(
    session: AsyncSession, name: str, item_id: str, payload: dict[str, Any], user: AppUser
) -> dict[str, Any]:
    _ensure_known(name)
    if _is_value_list(name):
        setting = await _value_list_setting(session, name)
        items = list((setting.value or {}).get("items") or [])
        for item in items:
            if item.get("id") == item_id:
                if payload.get("name"):
                    item["name"] = str(payload["name"]).strip()
                if "code" in payload:
                    item["code"] = (str(payload["code"]).strip() or None) if payload[
                        "code"
                    ] is not None else None
                setting.value = {"items": items}
                setting.updated_by = user.id
                await session.commit()
                return item
        raise not_found("Элемент справочника не найден")

    model_map = {
        "federal_projects": (FederalProject, _fed_project_item),
        "interaction_types": (InteractionType, _interaction_type_item),
        "universities_registry": (University, _university_item),
        "products": (Product, _product_item),
    }
    model, serializer = model_map[name]
    try:
        row = await session.get(model, uuid.UUID(item_id))
    except ValueError:
        row = None
    if row is None:
        raise not_found("Элемент справочника не найден")
    editable = {"name", "code", "description", "starts_on", "ends_on", "is_active",
                "short_name", "inn", "kpp", "region", "city", "website"}
    for key, value in payload.items():
        if key in editable and hasattr(row, key):
            setattr(row, key, value)
    await session.commit()
    return serializer(row)


async def delete_item(
    session: AsyncSession, name: str, item_id: str, user: AppUser
) -> None:
    _ensure_known(name)
    if _is_value_list(name):
        setting = await _value_list_setting(session, name)
        items = list((setting.value or {}).get("items") or [])
        filtered = [item for item in items if item.get("id") != item_id]
        if len(filtered) == len(items):
            raise not_found("Элемент справочника не найден")
        setting.value = {"items": filtered}
        setting.updated_by = user.id
        await session.commit()
        return

    model_map = {
        "federal_projects": FederalProject,
        "interaction_types": InteractionType,
        "universities_registry": University,
        "products": Product,
    }
    model = model_map[name]
    try:
        row = await session.get(model, uuid.UUID(item_id))
    except ValueError:
        row = None
    if row is None:
        raise not_found("Элемент справочника не найден")
    # деактивация вместо физического удаления: на элементы могут ссылаться сущности
    row.is_active = False
    await session.commit()
