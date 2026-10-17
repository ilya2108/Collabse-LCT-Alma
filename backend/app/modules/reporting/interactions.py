"""Отчёт «Взаимодействия за период» — таблица с выбором колонок (ТЗ ФТ4).

Третья вкладка страницы «Активность КАМов»: строка — заявка, с которой шла работа в
периоде, колонки — обязательный набор ТЗ (`COLUMNS`, группа «ТЗ»: вуз, направление,
продукт, этап на конец периода, ответственный) плюс программа, заявка, процесс и даты.
Фильтры — вузы, ИТ-направления, ИТ-продукты, программы, КАМы, статус (этап на конец
периода), процесс B2B/B2C.

Одна семантика периода с отчётом КАМов — без второго определения «за период»:
- период, «данные на» (`as_of`) и границы по МСК — `kam_activity.resolve_params/_bounds`;
- какие заявки «в периоде» (D3) и этап на конец периода — тот же запрос
  `kam_activity.deal_rows_stmt` (этап по истории, вход в этап без строк перевода схемы);
- взаимодействия — собственные действия ответственного КАМа (переходы, комментарии, файлы);
- зона видимости — `report_scope` и `_resolve_scope` (явный 403 на процесс вне области и
  на чужой `kam_id` при области «только свои»).

ИТ-направление заявки — из справочника `it_direction`: направление её программы, без него —
направления её ИТ-продукта через запятую (`crm.directions.direction_names`); фильтр
`direction_id` — по тому же правилу (`direction_match`), поэтому строки с выбранным
направлением всегда показывают его в колонке. Нет ни того, ни другого — `null` («—»).

Выгрузка — `POST /export/jobs {entity_type: "report:interactions", columns: [...]}`
(`interactions_export.py`): XLSX и PDF (флагом не закрыт, как PDF отчёта КАМов), CSV/JSON.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import orjson
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.access import DealScope
from app.core.cache import get_cache
from app.core.errors import validation_error
from app.models.crm import Product, Program
from app.models.deal import Deal
from app.models.user import AppUser
from app.modules.crm.directions import direction_names, direction_refs
from app.modules.reporting import kam_activity as ka
from app.modules.reporting.service import REPORT_CACHE_TTL_SECONDS, _envelope
from app.modules.workflow.service import _global_stuck_default  # каскад порога (Р-5)

CODE = "interactions"
ENTITY_TYPE = "report:interactions"
MAX_ROWS = 5000  # предохранитель ответа API; выгрузка — без него
B2C_SUFFIX = " (B2C)"


@dataclass(frozen=True)
class ColumnDef:
    key: str
    label: str
    default: bool = False
    group: str = "ТЗ"  # «ТЗ» — колонки из требований, «Дополнительно» — сверх них
    width: float = 1.0  # доля ширины в PDF


# Порядок — порядок колонок в UI и файлах; первые пять — набор ТЗ ФТ4
COLUMNS: tuple[ColumnDef, ...] = (
    ColumnDef("university", "Наименование вуза", True, width=2.4),
    ColumnDef("it_direction", "ИТ-направление", True, width=1.3),
    ColumnDef("product", "ИТ-продукт", True, width=1.8),
    ColumnDef("status", "Статус работы с вузом", True, width=1.7),
    ColumnDef("responsible", "Ответственный", True, width=1.8),
    ColumnDef("program", "Программа", group="Дополнительно", width=1.8),
    ColumnDef("title", "Заявка", group="Дополнительно", width=2.4),
    ColumnDef("workflow_type", "Процесс", group="Дополнительно", width=0.8),
    ColumnDef("created_at", "Дата создания", True, group="Даты", width=1.1),
    ColumnDef("stage_since", "В статусе с", True, group="Даты", width=1.1),
    ColumnDef("last_contact_at", "Последний контакт", True, group="Даты", width=1.2),
    ColumnDef("days_on_stage", "Дней в статусе", group="Даты", width=0.9),
    ColumnDef("closed_in_period", "Итог в периоде", group="Дополнительно", width=1.0),
    ColumnDef("interactions", "Взаимодействий за период", group="Дополнительно", width=1.1),
    ColumnDef("amount", "Сумма, ₽", group="Дополнительно", width=1.1),
)
COLUMNS_BY_KEY: dict[str, ColumnDef] = {column.key: column for column in COLUMNS}
DEFAULT_COLUMNS: tuple[str, ...] = tuple(column.key for column in COLUMNS if column.default)

DEFINITIONS: tuple[str, ...] = (
    "Строка — заявка, с которой шла работа в периоде: создана в периоде, была в работе на "
    "его начало или конец, перешла на этап в периоде или по ней были действия "
    "ответственного КАМа. Архивные заявки не входят.",
    "Статус работы с вузом — этап заявки на конец периода по истории переходов; «В статусе "
    "с» — вход в этот этап (перевод схемы процесса и события LMS его не сдвигают).",
    "Взаимодействий за период — собственные действия ответственного КАМа: ручные переходы, "
    "комментарии и файлы заявки. Ответственный — текущий ответственный за заявку.",
    "Период — по московскому времени, с 00:00 первого дня по 24:00 последнего.",
    "ИТ-направление — направление программы заявки; если у программы оно не задано или "
    "программы нет — направления ИТ-продукта заявки через запятую. Фильтр по направлению "
    "работает по тому же правилу.",
)


def resolve_columns(requested: Sequence[str] | None) -> tuple[str, ...]:
    """Колонки выгрузки: по умолчанию — набор ТЗ и даты; неизвестная колонка — 400."""
    if not requested:
        return DEFAULT_COLUMNS
    unknown = [key for key in requested if key not in COLUMNS_BY_KEY]
    if unknown:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "columns",
                    "code": "unknown_column",
                    "message": (
                        f"Неизвестные колонки: {', '.join(unknown)}. "
                        f"Доступно: {', '.join(COLUMNS_BY_KEY)}"
                    ),
                }
            ],
        )
    return tuple(dict.fromkeys(requested))


def columns_payload() -> list[dict[str, Any]]:
    return [
        {"key": c.key, "label": c.label, "default": c.default, "group": c.group} for c in COLUMNS
    ]


# --------------------------------------------------------------------------------------
# Параметры
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InteractionsParams:
    """Фильтры отчёта: период, процесс, КАМы, вузы и ИТ-направления — общие с отчётом
    КАМов (`base`)."""

    base: ka.KamActivityParams = field(default_factory=ka.KamActivityParams)
    product_ids: tuple[uuid.UUID, ...] = ()
    program_ids: tuple[uuid.UUID, ...] = ()
    stage_ids: tuple[uuid.UUID, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            **self.base.as_json(),
            "product_id": sorted(str(item) for item in self.product_ids),
            "program_id": sorted(str(item) for item in self.program_ids),
            "stage_id": sorted(str(item) for item in self.stage_ids),
        }

    @classmethod
    def from_filters(cls, filters: Mapping[str, Any]) -> InteractionsParams:
        """Фильтры выгрузки: ключи query-контракта и URL-состояния страницы."""
        base = ka.KamActivityParams.from_filters(filters)
        errors: list[dict[str, Any]] = []

        def _uuids(*keys: str) -> tuple[uuid.UUID, ...]:
            result: list[uuid.UUID] = []
            for key in keys:
                raw = filters.get(key)
                if raw in (None, ""):
                    continue
                for item in raw if isinstance(raw, list | tuple) else [raw]:
                    try:
                        result.append(item if isinstance(item, uuid.UUID) else uuid.UUID(str(item)))
                    except ValueError:
                        errors.append(
                            {
                                "field": keys[0],
                                "code": "invalid_uuid",
                                "message": f"Некорректный идентификатор «{item}»",
                            }
                        )
            return tuple(result)

        params = cls(
            base=base,
            product_ids=_uuids("product_id", "product"),
            program_ids=_uuids("program_id", "program"),
            stage_ids=_uuids("stage_id", "status"),
        )
        if errors:
            raise validation_error("Ошибка валидации данных", details=errors)
        return params


def resolve_params(params: InteractionsParams, *, today: Any) -> InteractionsParams:
    """Период и фильтры отчёта КАМов + лимит числа продуктов, программ и этапов (400)."""
    base = ka.resolve_params(params.base, today=today)
    errors = [
        {
            "field": name,
            "code": "too_many_values",
            "message": f"Не больше {ka.MAX_FILTER_IDS} значений фильтра",
        }
        for name, values in (
            ("product_id", params.product_ids),
            ("program_id", params.program_ids),
            ("stage_id", params.stage_ids),
        )
        if len(set(values)) > ka.MAX_FILTER_IDS
    ]
    if errors:
        raise validation_error("Ошибка валидации данных", details=errors)
    return InteractionsParams(
        base=base,
        product_ids=ka._dedup(params.product_ids),
        program_ids=ka._dedup(params.program_ids),
        stage_ids=ka._dedup(params.stage_ids),
    )


# --------------------------------------------------------------------------------------
# Запрос и строки
# --------------------------------------------------------------------------------------


def interactions_stmt(
    params: InteractionsParams,
    bounds: ka.PeriodBounds,
    default_days: int,
    scope: DealScope | None = None,
) -> Any:
    """Запрос отчёта КАМов (D3, этап на конец периода, взаимодействия) + ИТ-направление,
    продукт, программа и ответственный; фильтры продуктов и программ — по колонкам заявки,
    направлений — в `deal_rows_stmt` (`_apply_deal_filters`)."""
    where = []
    if params.product_ids:
        where.append(Deal.product_id.in_(params.product_ids))
    if params.program_ids:
        where.append(Deal.program_id.in_(params.program_ids))
    responsible = aliased(AppUser, name="responsible")
    return (
        ka.deal_rows_stmt(
            params.base, bounds, default_days, scope, stage_ids=params.stage_ids, where=where
        )
        .outerjoin(Product, Product.id == Deal.product_id)
        .outerjoin(Program, Program.id == Deal.program_id)
        .outerjoin(responsible, responsible.id == Deal.responsible_user_id)
        .add_columns(
            direction_names(Deal.program_id, Deal.product_id).label("it_direction"),
            Deal.product_id.label("product_id"),
            Product.name.label("product_name"),
            Deal.program_id.label("program_id"),
            Program.name.label("program_name"),
            responsible.full_name.label("responsible_full_name"),
            responsible.username.label("responsible_username"),
        )
    )


def _iso_date(value: Any) -> str | None:
    return value.astimezone(ka.REPORT_TZ).date().isoformat() if value is not None else None


def interaction_row(mapping: Mapping[str, Any], bounds: ka.PeriodBounds) -> dict[str, Any]:
    """Строка отчёта: те же производные метрики, что у заявки в дереве отчёта КАМов."""
    deal = ka._deal_row(mapping)
    item = ka.request_item(deal, bounds)
    interactions = item["interactions"]
    responsible = mapping["responsible_full_name"] or mapping["responsible_username"] or "—"
    return {
        "request_id": item["request_id"],
        "title": deal.title,
        "workflow_type": deal.pipeline,
        "university_id": str(deal.university_id) if deal.university_id else None,
        "university": deal.university_name,
        "university_short": deal.university_short,
        "client": deal.client if deal.pipeline == "b2c" else None,
        "it_direction": mapping.get("it_direction"),
        "product_id": str(mapping["product_id"]) if mapping["product_id"] else None,
        "product": mapping["product_name"],
        "program_id": str(mapping["program_id"]) if mapping["program_id"] else None,
        "program": mapping["program_name"],
        "stage": {
            key: item["stage"][key]
            for key in ("id", "code", "name", "color", "is_terminal", "outcome", "deleted")
        },
        "responsible_id": str(deal.responsible_id),
        "responsible": responsible,
        "created_at": item["created_at"],
        "stage_since": item["stage_since"],
        "days_on_stage": item["days_on_stage"],
        "threshold_days": item["threshold_days"],
        "is_stuck": item["is_stuck"],
        "closed_in_period": item["closed_in_period"],
        "interactions": {
            "transitions": interactions["transitions"],
            "returns": interactions["returns"],
            "comments": interactions["comments"],
            "files": interactions["files"],
            "total": interactions["transitions"] + interactions["comments"] + interactions["files"],
        },
        "last_contact_at": item["last_contact_at"],
        "amount": item["amount"],
    }


def _sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    place = row["university"] or row["client"] or ""
    return (
        row["workflow_type"] != "b2b" or row["university"] is None,  # B2C — в конце
        ka._name_key(place),
        ka._name_key(row["title"]),
    )


def totals_of(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    amount = sum((Decimal(row["amount"]) for row in rows if row["amount"]), Decimal(0))
    return {
        "requests": len(rows),
        "universities": len({row["university_id"] for row in rows if row["university_id"]}),
        "responsibles": len({row["responsible_id"] for row in rows}),
        "with_interactions": sum(1 for row in rows if row["interactions"]["total"] > 0),
        "interactions": sum(row["interactions"]["total"] for row in rows),
        "stuck": sum(1 for row in rows if row["is_stuck"]),
        "won": sum(1 for row in rows if row["closed_in_period"] == "won"),
        "lost": sum(1 for row in rows if row["closed_in_period"] == "lost"),
        "amount": format(amount, "f"),
    }


async def stage_options(
    session: AsyncSession, workflow_types: Sequence[str], *, default_days: int
) -> list[dict[str, Any]]:
    """Этапы процессов отчёта для фильтра «Статус» (без мягко удалённых)."""
    stages = await ka.fetch_stages(session, workflow_types, default_days=default_days)
    options: list[dict[str, Any]] = []
    for workflow_type in workflow_types:
        for stage in stages.get(workflow_type, []):
            if stage.deleted:
                continue
            options.append(
                {
                    "id": str(stage.stage_id),
                    "code": stage.code,
                    "name": stage.name,
                    "color": stage.color,
                    "workflow_type": workflow_type,
                    "is_terminal": stage.is_terminal,
                }
            )
    return options


# --------------------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------------------


def _cache_key(params: InteractionsParams, scope: DealScope) -> str:
    digest = hashlib.sha1(
        orjson.dumps(
            {"params": params.as_json(), "scope": scope.cache_key()},
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()
    return f"dash:{CODE}:{digest}"


async def interactions_body(
    session: AsyncSession,
    params: InteractionsParams,
    bounds: ka.PeriodBounds,
    scope: DealScope,
    *,
    limit: int | None = MAX_ROWS,
) -> dict[str, Any]:
    default_days = await _global_stuck_default(session)
    result = await session.execute(interactions_stmt(params, bounds, default_days, scope))
    rows = sorted((interaction_row(row, bounds) for row in result.mappings()), key=_sort_key)
    truncated = limit is not None and len(rows) > limit
    body: dict[str, Any] = {
        "period": bounds.as_json(),
        "scope": ka._scope_payload(scope),
        "columns": columns_payload(),
        "default_columns": list(DEFAULT_COLUMNS),
        "stages": await stage_options(
            session, ka._visible_workflows(params.base, scope), default_days=default_days
        ),
        "totals": totals_of(rows),
        "truncated": truncated,
        "rows": rows[:limit] if truncated else rows,
    }
    if params.base.direction_ids:
        # подписи выбранных направлений — для шапки файлов и чипов фильтра
        body["directions"] = await direction_refs(session, params.base.direction_ids)
    return body


def prepare(
    viewer: AppUser, params: InteractionsParams, scope: DealScope | None = None
) -> tuple[InteractionsParams, DealScope, ka.PeriodBounds]:
    """Дефолты и валидация периода (400), область и 403 — те же, что у отчёта КАМов."""
    now = ka._now()
    params = resolve_params(params, today=now.astimezone(ka.REPORT_TZ).date())
    scope = scope if scope is not None else ka.viewer_scope(viewer)
    base = ka._resolve_scope(viewer, params.base, scope)
    params = InteractionsParams(
        base=base,
        product_ids=params.product_ids,
        program_ids=params.program_ids,
        stage_ids=params.stage_ids,
    )
    return params, scope, ka.resolve_bounds(base, now=now)


async def build_interactions(
    session: AsyncSession,
    viewer: AppUser,
    params: InteractionsParams,
    *,
    scope: DealScope | None = None,
    for_export: bool = False,
) -> dict[str, Any]:
    """Отчёт с кэшем cache-aside (TTL 60 с); `for_export` — все строки, мимо кэша."""
    params, scope, bounds = prepare(viewer, params, scope)
    if for_export:
        body = await interactions_body(session, params, bounds, scope, limit=None)
        return _envelope(CODE, params, body)  # type: ignore[arg-type]
    cache = get_cache()
    key = _cache_key(params, scope)
    cached = await cache.get_json(key)
    if cached is not None:
        return cached
    body = await interactions_body(session, params, bounds, scope)
    result = _envelope(CODE, params, body)  # type: ignore[arg-type]
    await cache.set_json(key, result, REPORT_CACHE_TTL_SECONDS)
    return result
