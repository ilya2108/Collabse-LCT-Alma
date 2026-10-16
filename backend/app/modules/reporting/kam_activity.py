"""Отчёт «Активность КАМов» (api-contract.md §7.2.1, ux.md §6.4).

Отвечает на вопрос экспертов: какой КАМ за какой период с какими вузами взаимодействовал
и на каких этапах с ними остановился. Два представления одного набора данных:
- детализация (А, руководителю КАМов) — дерево КАМ → вузы → заявки;
- сводка (Б, бизнес-заказчику) — KPI с дельтами к прошлому периоду той же длины,
  конверсия по этапам, «где застревают», динамика взаимодействий, разрез по КАМам,
  студенты на программах из пула талантов и детерминированные текстовые выводы.

Определения метрик (одни и те же в UI, «Как считается», XLSX и PDF):
- период — `[from 00:00 МСК; to+1 00:00 МСК)`, «данные на» `as_of = min(конец, сейчас)`;
- этап остановки — этап заявки на момент `as_of`, восстановленный по `deal_stage_history`
  (для прошлых периодов это этап на конец периода, а не сегодняшний);
- взаимодействие — СОБСТВЕННОЕ действие ответственного КАМа в периоде: ручной переход
  (`kind='manual'`), комментарий, файл заявки, мероприятие с вузом (слот в периоде).
  Атрибуция по автору действия: переходы системы (перевод схемы `system_migration`,
  LMS, импорт) меняют этап, но активностью не считаются; действия других пользователей
  (руководителя, админа) в заявке КАМа — тоже;
- заявка числится за текущим ответственным, как во всех отчётах и в зоне видимости;
- зависла — этап нетерминальный и на этапе дольше порога (этап → воронка → глобальный);
- выиграно / отказ — этап остановки терминальный и вход в него в периоде;
- охват — закреплённые вузы с ≥1 взаимодействием в периоде / все закреплённые;
- мягко удалённый этап показывается под историческим названием с пометкой «(удалён)».

Зона видимости — область отчётов RBAC `report_scope(viewer)` (`DealScope`: процессы ролей
с `reports:read`, «по всем КАМам» только с `reports:all`); единственная точка проверки —
`_resolve_scope` (явный 403 на процесс вне области и на чужой `kam_id`). Основной запрос
проходит через `scope.apply`, мероприятия и закреплённые вузы (сущности B2B) — только при
процессе b2b в области. КАМы отчёта — роли с правом `requests:kam` в процессе.
`_envelope` и TTL кэша — из `service.py` того же пакета (приватные имена внутри пакета
`reporting`); ключ кэша `dash:kam-activity[-summary]:{sha1(params + scope.cache_key())}`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import orjson
import sqlalchemy as sa
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.access import DealScope, get_catalog_snapshot
from app.core.cache import get_cache
from app.core.errors import forbidden, validation_error
from app.models.crm import Counterparty, Program, University
from app.models.deal import Deal, DealComment, DealStageHistory
from app.models.service import FileObject
from app.models.talent import Activity, ActivitySchedule, Student, StudentStatusHistory
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.reporting.service import (
    FUNNEL_COLORS,
    FUNNEL_LABELS,
    REPORT_CACHE_TTL_SECONDS,
    _envelope,
    report_scope,
)
from app.modules.workflow.service import _global_stuck_default  # каскад порога (Р-5)

# фиксированный сдвиг без DST: tzdata в slim-образе не нужен (D7)
REPORT_TZ = timezone(timedelta(hours=3), "MSK")
DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 731
MAX_FILTER_IDS = 50
MAX_DETAIL_REQUESTS = 5000  # предохранитель размера дерева детализации
TOP_STUCK_LIMIT = 10
TOP_PROGRAMS_LIMIT = 10
HIGHLIGHTS_LIMIT = 5

CODE_DETAIL = "kam-activity"
CODE_SUMMARY = "kam-activity-summary"

WORKFLOW_TYPES: tuple[str, ...] = ("b2b", "b2c")
WORKFLOW_LABELS = {"b2b": "B2B", "b2c": "B2C"}
B2C_NODE_NAME = "B2C-клиенты (без вуза)"
DELETED_SUFFIX = " (удалён)"

# П/К/Ф/М — порядок в UI, XLSX и PDF; returns — подмножество переходов, в total не входит
INTERACTION_KEYS: tuple[str, ...] = ("transitions", "returns", "comments", "files", "activities")
INTERACTION_LABELS = {
    "transitions": "Переходы",
    "returns": "Возвраты",
    "comments": "Комментарии",
    "files": "Файлы",
    "activities": "Мероприятия",
    "new_requests": "Новые заявки",
}
FUNNEL_ORDER: tuple[str, ...] = ("candidate", "studying", "graduate", "talent_pool")

Granularity = Literal["day", "week", "month"]
_GRANULARITIES: tuple[str, ...] = ("day", "week", "month")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Параметры, период, зона видимости
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KamActivityParams:
    """Фильтры отчёта; `as_json` — каноническая форма (id отсортированы) для кэша."""

    date_from: date | None = None
    date_to: date | None = None
    workflow_type: str | None = None  # 'b2b' | 'b2c' | None
    kam_ids: tuple[uuid.UUID, ...] = ()
    university_ids: tuple[uuid.UUID, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
            "workflow_type": self.workflow_type,
            "kam_id": sorted(str(item) for item in self.kam_ids),
            "university_id": sorted(str(item) for item in self.university_ids),
        }

    @classmethod
    def from_filters(cls, filters: Mapping[str, Any]) -> KamActivityParams:
        """Фильтры выгрузки (`POST /export/jobs`): строка или список → tuple[UUID].

        Принимает ключи query-контракта (`from`, `to`, `workflow_type`, `kam_id`,
        `university_id`) и URL-состояния страницы (`wf`, `kam`, `university`).
        """
        errors: list[dict[str, Any]] = []

        def _date(*keys: str) -> date | None:
            for key in keys:
                raw = filters.get(key)
                if raw in (None, ""):
                    continue
                if isinstance(raw, date):
                    return raw
                try:
                    return date.fromisoformat(str(raw))
                except ValueError:
                    errors.append(
                        {
                            "field": keys[0],
                            "code": "invalid_date",
                            "message": f"Дата «{raw}» не в формате ГГГГ-ММ-ДД",
                        }
                    )
                    return None
            return None

        def _uuids(*keys: str) -> tuple[uuid.UUID, ...]:
            result: list[uuid.UUID] = []
            for key in keys:
                raw = filters.get(key)
                if raw in (None, ""):
                    continue
                items = raw if isinstance(raw, list | tuple) else [raw]
                for item in items:
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

        workflow_type = filters.get("workflow_type") or filters.get("wf") or None
        if workflow_type is not None and workflow_type not in WORKFLOW_TYPES:
            errors.append(
                {
                    "field": "workflow_type",
                    "code": "invalid_value",
                    "message": "Тип процесса — b2b или b2c",
                }
            )
            workflow_type = None
        params = cls(
            date_from=_date("from", "date_from"),
            date_to=_date("to", "date_to"),
            workflow_type=workflow_type,
            kam_ids=_uuids("kam_id", "kam"),
            university_ids=_uuids("university_id", "university"),
        )
        if errors:
            raise validation_error("Ошибка валидации данных", details=errors)
        return params


@dataclass(frozen=True)
class PeriodBounds:
    date_from: date
    date_to: date
    start: datetime  # from 00:00 МСК, tz-aware
    end: datetime  # to+1 00:00 МСК (полуинтервал)
    as_of: datetime  # min(end, now) — «данные на»

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    def as_json(self) -> dict[str, Any]:
        return {
            "from": self.date_from.isoformat(),
            "to": self.date_to.isoformat(),
            "as_of": self.as_of.isoformat(),
            "days": self.days,
        }


def _dedup(ids: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    return tuple(sorted(set(ids), key=str))


def resolve_params(params: KamActivityParams, *, today: date) -> KamActivityParams:
    """Дефолты (последние 30 дней по МСК) и валидация периода и фильтров (400)."""
    date_to = params.date_to or today
    date_from = params.date_from or (date_to - timedelta(days=DEFAULT_PERIOD_DAYS - 1))
    errors: list[dict[str, Any]] = []
    if date_from > date_to:
        errors.append(
            {
                "field": "from",
                "code": "period_inverted",
                "message": "Начало периода позже окончания",
            }
        )
    elif (date_to - date_from).days + 1 > MAX_PERIOD_DAYS:
        errors.append(
            {"field": "to", "code": "period_too_long", "message": "Период не длиннее двух лет"}
        )
    if date_from > today:
        errors.append(
            {
                "field": "from",
                "code": "period_in_future",
                "message": "Начало периода не может быть позже сегодняшнего дня",
            }
        )
    for name, values in (("kam_id", params.kam_ids), ("university_id", params.university_ids)):
        if len(set(values)) > MAX_FILTER_IDS:
            errors.append(
                {
                    "field": name,
                    "code": "too_many_values",
                    "message": f"Не больше {MAX_FILTER_IDS} значений фильтра",
                }
            )
    if params.workflow_type is not None and params.workflow_type not in WORKFLOW_TYPES:
        errors.append(
            {
                "field": "workflow_type",
                "code": "invalid_value",
                "message": "Тип процесса — b2b или b2c",
            }
        )
    if errors:
        raise validation_error("Ошибка валидации данных", details=errors)
    return replace(
        params,
        date_from=date_from,
        date_to=date_to,
        kam_ids=_dedup(params.kam_ids),
        university_ids=_dedup(params.university_ids),
    )


def _bounds(date_from: date, date_to: date, now: datetime) -> PeriodBounds:
    start = datetime.combine(date_from, time.min, REPORT_TZ)
    end = datetime.combine(date_to + timedelta(days=1), time.min, REPORT_TZ)
    return PeriodBounds(
        date_from=date_from, date_to=date_to, start=start, end=end, as_of=min(end, now)
    )


def resolve_bounds(params: KamActivityParams, *, now: datetime) -> PeriodBounds:
    assert params.date_from is not None and params.date_to is not None, "сначала resolve_params"
    return _bounds(params.date_from, params.date_to, now)


def previous_bounds(bounds: PeriodBounds) -> PeriodBounds:
    """Интервал той же длины непосредственно перед текущим (D8, как `_kpi`)."""
    prev_to = bounds.date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=bounds.days - 1)
    return _bounds(prev_from, prev_to, bounds.end)  # прошлый период целиком в прошлом


def pick_granularity(days: int) -> Granularity:
    if days <= 31:
        return "day"
    if days <= 184:
        return "week"
    return "month"


def viewer_scope(viewer: AppUser) -> DealScope:
    """Область отчёта зрителя — общая область отчётов RBAC (`reporting.service.report_scope`):
    процессы ролей с `reports:read`; «все КАМы» — при `reports:all` и записях `all`."""
    return report_scope(viewer)


def _rule(scope: DealScope, workflow_type: str) -> tuple[bool, uuid.UUID | None]:
    """(виден ли процесс, ответственный правила процесса) — для запросов вне `deal`."""
    return scope.owner_for(workflow_type)


def _visible_workflows(params: KamActivityParams, scope: DealScope) -> tuple[str, ...]:
    allowed = tuple(scope.workflow_types)
    if params.workflow_type:
        return tuple(wf for wf in allowed if wf == params.workflow_type)
    return tuple(wf for wf in WORKFLOW_TYPES if wf in allowed)


def _is_wide(params: KamActivityParams, scope: DealScope) -> bool:
    """Без фильтров по людям/вузам и без ограничения «только свои»."""
    return (
        not params.kam_ids
        and not params.university_ids
        and all(own is None for _wf, own in scope.rules)
    )


def _resolve_scope(
    viewer: AppUser, params: KamActivityParams, scope: DealScope | None = None
) -> KamActivityParams:
    """Единая точка проверки видимости (D10): явный 403 вместо тихого пустого ответа.

    Процесс вне области — 403 (правило коллекций RBAC); при области «только свои» чужой
    `kam_id` — 403, без фильтра подставляется сам зритель.
    """
    scope = scope if scope is not None else viewer_scope(viewer)
    if scope.is_empty:
        raise forbidden("Отчёты недоступны вашей роли")
    if params.workflow_type and params.workflow_type not in tuple(scope.workflow_types):
        label = WORKFLOW_LABELS.get(params.workflow_type, params.workflow_type)
        raise forbidden(f"Процесс {label} недоступен вашей роли")
    own = scope.own_user_id
    if own is not None:
        if any(kam_id != own for kam_id in params.kam_ids):
            raise forbidden("КАМ видит отчёт только по своим заявкам")
        if not params.kam_ids:
            params = replace(params, kam_ids=(own,))
    return params


def _cache_key(code: str, params: KamActivityParams, scope: DealScope) -> str:
    digest = hashlib.sha1(
        orjson.dumps(
            {"params": params.as_json(), "scope": scope.cache_key()},
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()
    return f"dash:{code}:{digest}"


# --------------------------------------------------------------------------------------
# Основной запрос: одна строка на заявку (D12)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DealRow:
    deal_id: uuid.UUID
    title: str
    pipeline: str
    university_id: uuid.UUID | None
    university_name: str | None
    university_short: str | None
    university_region: str | None
    counterparty_id: uuid.UUID | None
    client: str | None
    responsible_id: uuid.UUID
    amount: Decimal | None
    status: str
    created_at: datetime
    stage_id: uuid.UUID
    stage_code: str
    stage_name: str  # уже с пометкой «(удалён)» для мягко удалённого этапа
    stage_color: str | None
    stage_is_terminal: bool
    stage_outcome: str | None
    stage_sort: int
    stage_deleted: bool
    stage_since: datetime
    threshold: int
    transitions: int = 0
    returns: int = 0
    comments: int = 0
    files: int = 0
    last_contact_at: datetime | None = None


def _apply_deal_filters(
    stmt: sa.Select, params: KamActivityParams, scope: DealScope | None
) -> sa.Select:
    if params.workflow_type:
        stmt = stmt.where(Deal.pipeline == params.workflow_type)
    if params.kam_ids:
        stmt = stmt.where(Deal.responsible_user_id.in_(params.kam_ids))
    if params.university_ids:
        stmt = stmt.where(Deal.university_id.in_(params.university_ids))
    if scope is not None:
        stmt = scope.apply(stmt)
    return stmt


def deal_rows_stmt(
    params: KamActivityParams,
    bounds: PeriodBounds,
    default_days: int,
    scope: DealScope | None = None,
) -> sa.Select:
    """Заявки, с которыми шла работа или которые висели в периоде (D3), с метриками.

    Пять LATERAL: этап на `as_of` и на начало периода (последняя строка истории до даты,
    индекс `ix_dsh_deal`), собственные ручные переходы, комментарии и файлы
    ответственного КАМа (количество в периоде + последний момент до `as_of`).
    """
    start, end, as_of = bounds.start, bounds.end, bounds.as_of

    h_end_src = aliased(DealStageHistory, name="dsh_end")
    h_end = (
        select(
            h_end_src.to_stage_id.label("stage_id"),
            h_end_src.to_stage_name.label("stage_name"),
            h_end_src.created_at.label("since"),
        )
        .where(h_end_src.deal_id == Deal.id, h_end_src.created_at < as_of)
        .order_by(h_end_src.created_at.desc(), h_end_src.id.desc())
        .limit(1)
        .lateral("h_end")
    )
    h_start_src = aliased(DealStageHistory, name="dsh_start")
    h_start = (
        select(h_start_src.to_stage_id.label("stage_id"))
        .where(h_start_src.deal_id == Deal.id, h_start_src.created_at < start)
        .order_by(h_start_src.created_at.desc(), h_start_src.id.desc())
        .limit(1)
        .lateral("h_start")
    )

    ev_src = aliased(DealStageHistory, name="dsh_own")
    ev_in_period = and_(ev_src.created_at >= start, ev_src.created_at < as_of)
    ev = (
        select(
            func.count()
            .filter(and_(ev_src.from_stage_id.is_not(None), ev_in_period))
            .label("transitions"),
            func.count()
            .filter(
                and_(ev_src.from_stage_id.is_not(None), ev_src.is_return.is_(True), ev_in_period)
            )
            .label("returns"),
            func.max(ev_src.created_at).label("last_at"),
        )
        .where(
            ev_src.deal_id == Deal.id,
            ev_src.actor_user_id == Deal.responsible_user_id,
            ev_src.kind == "manual",
            ev_src.created_at < as_of,
        )
        .lateral("ev")
    )
    cm = (
        select(
            func.count()
            .filter(and_(DealComment.created_at >= start, DealComment.created_at < as_of))
            .label("comments"),
            func.max(DealComment.created_at).label("last_at"),
        )
        .where(
            DealComment.deal_id == Deal.id,
            DealComment.author_user_id == Deal.responsible_user_id,
            DealComment.deleted_at.is_(None),
            DealComment.created_at < as_of,
        )
        .lateral("cm")
    )
    fl = (
        select(
            func.count()
            .filter(and_(FileObject.created_at >= start, FileObject.created_at < as_of))
            .label("files"),
            func.max(FileObject.created_at).label("last_at"),
        )
        .where(
            FileObject.entity_type == "deal",
            FileObject.entity_id == Deal.id,
            FileObject.uploaded_by == Deal.responsible_user_id,
            FileObject.deleted_at.is_(None),
            FileObject.created_at < as_of,
        )
        .lateral("fl")
    )

    end_stage = aliased(WorkflowStage, name="end_stage")
    start_stage = aliased(WorkflowStage, name="start_stage")
    stage_since = func.coalesce(h_end.c.since, Deal.stage_entered_at)
    threshold = func.coalesce(
        end_stage.stuck_threshold_days, Workflow.stuck_threshold_days, default_days
    )
    stmt = (
        select(
            Deal.id.label("deal_id"),
            Deal.title.label("title"),
            Deal.pipeline.label("pipeline"),
            Deal.university_id.label("university_id"),
            University.name.label("university_name"),
            University.short_name.label("university_short"),
            University.region.label("university_region"),
            Deal.counterparty_id.label("counterparty_id"),
            Counterparty.display_name.label("client"),
            Deal.responsible_user_id.label("responsible_id"),
            Deal.amount.label("amount"),
            Deal.status.label("status"),
            Deal.created_at.label("created_at"),
            end_stage.id.label("stage_id"),
            end_stage.code.label("stage_code"),
            end_stage.name.label("stage_current_name"),
            h_end.c.stage_name.label("stage_history_name"),
            end_stage.color.label("stage_color"),
            end_stage.is_terminal.label("stage_is_terminal"),
            end_stage.terminal_outcome.label("stage_outcome"),
            end_stage.sort_order.label("stage_sort"),
            end_stage.deleted_at.label("stage_deleted_at"),
            stage_since.label("stage_since"),
            threshold.label("threshold"),
            ev.c.transitions,
            ev.c.returns,
            cm.c.comments,
            fl.c.files,
            func.greatest(ev.c.last_at, cm.c.last_at, fl.c.last_at).label("last_contact_at"),
        )
        .select_from(Deal)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .outerjoin(University, University.id == Deal.university_id)
        .outerjoin(Counterparty, Counterparty.id == Deal.counterparty_id)
        .outerjoin(h_end, sa.true())
        .outerjoin(h_start, sa.true())
        .join(end_stage, end_stage.id == func.coalesce(h_end.c.stage_id, Deal.stage_id))
        .outerjoin(start_stage, start_stage.id == h_start.c.stage_id)
        .outerjoin(ev, sa.true())
        .outerjoin(cm, sa.true())
        .outerjoin(fl, sa.true())
        .where(
            Deal.status != "archived",
            Deal.created_at < end,
            or_(
                Deal.created_at >= start,  # (а) создана в периоде
                and_(  # (б) на начало периода была в работе
                    h_start.c.stage_id.is_not(None), start_stage.is_terminal.is_(False)
                ),
                end_stage.is_terminal.is_(False),  # (в) на конец периода в работе
                stage_since >= start,  # (г) вошла в этап остановки в периоде
                ev.c.transitions > 0,  # (д) собственные действия КАМа в периоде
                cm.c.comments > 0,
                fl.c.files > 0,
            ),
        )
        .order_by(Deal.responsible_user_id, University.name.asc().nulls_last(), Deal.created_at)
    )
    return _apply_deal_filters(stmt, params, scope)


def _deal_row(mapping: Mapping[str, Any]) -> DealRow:
    deleted = mapping["stage_deleted_at"] is not None
    name = mapping["stage_current_name"]
    if deleted:
        name = f"{mapping['stage_history_name'] or name}{DELETED_SUFFIX}"
    return DealRow(
        deal_id=mapping["deal_id"],
        title=mapping["title"],
        pipeline=mapping["pipeline"],
        university_id=mapping["university_id"],
        university_name=mapping["university_name"],
        university_short=mapping["university_short"],
        university_region=mapping["university_region"],
        counterparty_id=mapping["counterparty_id"],
        client=mapping["client"],
        responsible_id=mapping["responsible_id"],
        amount=mapping["amount"],
        status=mapping["status"],
        created_at=mapping["created_at"],
        stage_id=mapping["stage_id"],
        stage_code=mapping["stage_code"],
        stage_name=name,
        stage_color=mapping["stage_color"],
        stage_is_terminal=bool(mapping["stage_is_terminal"]),
        stage_outcome=mapping["stage_outcome"],
        stage_sort=int(mapping["stage_sort"] or 0),
        stage_deleted=deleted,
        stage_since=mapping["stage_since"],
        threshold=int(mapping["threshold"]),
        transitions=int(mapping["transitions"] or 0),
        returns=int(mapping["returns"] or 0),
        comments=int(mapping["comments"] or 0),
        files=int(mapping["files"] or 0),
        last_contact_at=mapping["last_contact_at"],
    )


async def fetch_deal_rows(
    session: AsyncSession,
    params: KamActivityParams,
    bounds: PeriodBounds,
    scope: DealScope | None = None,
    *,
    default_days: int | None = None,
) -> list[DealRow]:
    if default_days is None:
        default_days = await _global_stuck_default(session)
    result = await session.execute(deal_rows_stmt(params, bounds, default_days, scope))
    return [_deal_row(row) for row in result.mappings()]


# --------------------------------------------------------------------------------------
# Вспомогательные запросы: мероприятия, закреплённые вузы, люди
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityStat:
    responsible_id: uuid.UUID
    university_id: uuid.UUID
    name: str
    short_name: str | None
    region: str | None
    activities: int  # слотов в периоде (до as_of)
    last_at: datetime | None  # последний слот до as_of за всё время


@dataclass(frozen=True)
class UniversityRef:
    university_id: uuid.UUID
    name: str
    short_name: str | None
    region: str | None
    kam_id: uuid.UUID


@dataclass(frozen=True)
class PersonRef:
    user_id: uuid.UUID
    full_name: str
    roles: tuple[str, ...] = ()
    is_active: bool = True


def _b2b_rule(params: KamActivityParams, scope: DealScope) -> tuple[bool, uuid.UUID | None]:
    """Мероприятия и закреплённые вузы — сущности B2B: видны, если виден процесс b2b."""
    if params.workflow_type == "b2c":
        return False, None
    return _rule(scope, "b2b")


def activity_stats_stmt(
    params: KamActivityParams, bounds: PeriodBounds, own: uuid.UUID | None = None
) -> sa.Select:
    in_period = and_(
        ActivitySchedule.starts_at >= bounds.start, ActivitySchedule.starts_at < bounds.as_of
    )
    stmt = (
        select(
            Activity.responsible_user_id.label("responsible_id"),
            Activity.university_id.label("university_id"),
            University.name.label("name"),
            University.short_name.label("short_name"),
            University.region.label("region"),
            func.count(ActivitySchedule.id).filter(in_period).label("activities"),
            func.max(ActivitySchedule.starts_at).label("last_at"),
        )
        .select_from(Activity)
        .join(ActivitySchedule, ActivitySchedule.activity_id == Activity.id)
        .join(University, University.id == Activity.university_id)
        .where(
            Activity.university_id.is_not(None),
            Activity.responsible_user_id.is_not(None),
            ActivitySchedule.starts_at < bounds.as_of,
        )
        .group_by(
            Activity.responsible_user_id,
            Activity.university_id,
            University.name,
            University.short_name,
            University.region,
        )
    )
    if params.kam_ids:
        stmt = stmt.where(Activity.responsible_user_id.in_(params.kam_ids))
    if params.university_ids:
        stmt = stmt.where(Activity.university_id.in_(params.university_ids))
    if own is not None:
        stmt = stmt.where(Activity.responsible_user_id == own)
    return stmt


async def fetch_activity_stats(
    session: AsyncSession, params: KamActivityParams, bounds: PeriodBounds, scope: DealScope
) -> list[ActivityStat]:
    visible, own = _b2b_rule(params, scope)
    if not visible:
        return []
    result = await session.execute(activity_stats_stmt(params, bounds, own))
    return [
        ActivityStat(
            responsible_id=row["responsible_id"],
            university_id=row["university_id"],
            name=row["name"],
            short_name=row["short_name"],
            region=row["region"],
            activities=int(row["activities"] or 0),
            last_at=row["last_at"],
        )
        for row in result.mappings()
    ]


async def fetch_assigned_universities(
    session: AsyncSession, params: KamActivityParams, scope: DealScope
) -> list[UniversityRef]:
    """Закреплённые вузы — знаменатель охвата (D5). Закрепление не историческое."""
    visible, own = _b2b_rule(params, scope)
    if not visible:
        return []
    stmt = select(
        University.id,
        University.name,
        University.short_name,
        University.region,
        University.kam_user_id,
    ).where(University.is_active.is_(True), University.kam_user_id.is_not(None))
    if params.kam_ids:
        stmt = stmt.where(University.kam_user_id.in_(params.kam_ids))
    if params.university_ids:
        stmt = stmt.where(University.id.in_(params.university_ids))
    if own is not None:
        stmt = stmt.where(University.kam_user_id == own)
    return [
        UniversityRef(university_id=uid, name=name, short_name=short, region=region, kam_id=kam_id)
        for uid, name, short, region, kam_id in (await session.execute(stmt)).all()
    ]


def kam_role_codes(params: KamActivityParams, scope: DealScope) -> tuple[str, ...]:
    """Роли КАМов в процессах отчёта: право `requests:kam` в процессе (снимок каталога)."""
    catalog = get_catalog_snapshot()
    codes: set[str] = set()
    for workflow_type in _visible_workflows(params, scope):
        codes.update(catalog.role_codes_with("requests:kam", workflow_type))
    return tuple(sorted(codes))


async def fetch_people(
    session: AsyncSession, ids: Iterable[uuid.UUID], params: KamActivityParams, scope: DealScope
) -> dict[uuid.UUID, PersonRef]:
    """Люди строк отчёта: участники данных + активные КАМы (КАМ без активности виден).

    КАМ — пользователь с ролью, у которой есть `requests:kam` в процессе отчёта (каталог
    ролей RBAC), а не только системная роль `kam`: своя роль «КАМ B2C» тоже в отчёте.
    """
    wanted = set(ids) | set(params.kam_ids)
    conditions: list[sa.ColumnElement[bool]] = []
    if wanted:
        conditions.append(AppUser.id.in_(wanted))
    kam_roles = kam_role_codes(params, scope)
    if not params.kam_ids and not params.university_ids and kam_roles:
        # КАМ без активности тоже строка отчёта; при фильтре по вузам — только участники
        kam_condition = and_(AppUser.is_active.is_(True), AppUser.roles.overlap(list(kam_roles)))
        own = scope.own_user_id
        if own is not None:
            kam_condition = and_(kam_condition, AppUser.id == own)
        conditions.append(kam_condition)
    if not conditions:
        return {}
    users = (await session.execute(select(AppUser).where(or_(*conditions)))).scalars().all()
    return {
        user.id: PersonRef(
            user_id=user.id,
            full_name=user.full_name or user.username,
            roles=tuple(user.roles or ()),
            is_active=bool(user.is_active),
        )
        for user in users
    }


# --------------------------------------------------------------------------------------
# Сборка дерева КАМ → вузы → заявки (чистые функции)
# --------------------------------------------------------------------------------------


def _empty_interactions() -> dict[str, int]:
    return dict.fromkeys((*INTERACTION_KEYS, "total"), 0)


def _with_total(interactions: dict[str, int]) -> dict[str, int]:
    interactions["total"] = (
        interactions["transitions"]
        + interactions["comments"]
        + interactions["files"]
        + interactions["activities"]
    )
    return interactions


def _add(target: dict[str, int], source: Mapping[str, int]) -> None:
    for key in INTERACTION_KEYS:
        target[key] += int(source.get(key, 0))
    _with_total(target)


def _max_dt(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _money(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _conversion(won: int, lost: int) -> float | None:
    closed = won + lost
    return round(won * 100 / closed, 1) if closed else None


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def _name_key(name: str) -> str:
    return name.casefold().replace("ё", "е")


def request_item(row: DealRow, bounds: PeriodBounds) -> dict[str, Any]:
    """Строка заявки с производными метриками (дни на этапе, зависла, итог в периоде)."""
    on_stage = max(bounds.as_of - row.stage_since, timedelta(0))
    is_stuck = not row.stage_is_terminal and on_stage > timedelta(days=row.threshold)
    closed = (
        row.stage_outcome
        if row.stage_is_terminal and bounds.start <= row.stage_since < bounds.end
        else None
    )
    interactions = _with_total(
        {
            "transitions": row.transitions,
            "returns": row.returns,
            "comments": row.comments,
            "files": row.files,
            "activities": 0,  # мероприятия считаются на уровне вуза
            "total": 0,
        }
    )
    return {
        "request_id": str(row.deal_id),
        "title": row.title,
        "workflow_type": row.pipeline,
        "client": row.client if row.pipeline == "b2c" else None,
        "amount": _money(row.amount),
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "created_in_period": bounds.start <= row.created_at < bounds.end,
        "closed_in_period": closed,
        "stage": {
            "id": str(row.stage_id),
            "code": row.stage_code,
            "name": row.stage_name,
            "color": row.stage_color,
            "is_terminal": row.stage_is_terminal,
            "outcome": row.stage_outcome,
            "deleted": row.stage_deleted,
            "sort_order": row.stage_sort,
            "workflow_type": row.pipeline,
        },
        "stage_since": row.stage_since.isoformat(),
        "days_on_stage": on_stage.days,
        "threshold_days": row.threshold,
        "is_stuck": is_stuck,
        "interactions": interactions,
        "last_contact_at": _iso(row.last_contact_at),
        # служебное для сводки: сумма новой заявки (Decimal), в ответ не попадает
        "_amount": row.amount,
        "_created_at": row.created_at,
        "_last_contact": row.last_contact_at,
    }


@dataclass
class _UniNode:
    university_id: uuid.UUID | None
    name: str
    short_name: str | None
    region: str | None
    assigned: bool = False
    activities: int = 0
    activity_last: datetime | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)


def _stage_chips(requests: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    chips: dict[str, dict[str, Any]] = {}
    for request in requests:
        stage = request["stage"]
        chip = chips.get(stage["id"])
        if chip is None:
            chip = chips[stage["id"]] = {
                "id": stage["id"],
                "code": stage["code"],
                "name": stage["name"],
                "color": stage["color"],
                "is_terminal": stage["is_terminal"],
                "outcome": stage["outcome"],
                "workflow_type": stage["workflow_type"],
                "count": 0,
                "_sort": (
                    WORKFLOW_TYPES.index(stage["workflow_type"])
                    if stage["workflow_type"] in WORKFLOW_TYPES
                    else 9,
                    stage["sort_order"],
                ),
            }
        chip["count"] += 1
    ordered = sorted(chips.values(), key=lambda chip: chip["_sort"])
    for chip in ordered:
        chip.pop("_sort")
    return ordered


def _merge_chips(chip_lists: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for chips in chip_lists:
        for chip in chips:
            if chip["id"] not in merged:
                merged[chip["id"]] = dict(chip)
                order.append(chip["id"])
            else:
                merged[chip["id"]]["count"] += chip["count"]
    return sorted((merged[key] for key in order), key=lambda chip: (-chip["count"], chip["name"]))


def _public_request(request: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in request.items() if not key.startswith("_")}


def _finalize_node(node: _UniNode, *, include_requests: bool) -> dict[str, Any]:
    interactions = _empty_interactions()
    last_contact = node.activity_last
    for request in node.requests:
        _add(interactions, request["interactions"])
        last_contact = _max_dt(last_contact, request["_last_contact"])
    interactions["activities"] += node.activities
    _with_total(interactions)
    requests = sorted(
        node.requests,
        key=lambda r: (not r["is_stuck"], -r["days_on_stage"], _name_key(r["title"])),
    )
    return {
        "university_id": str(node.university_id) if node.university_id else None,
        "name": node.name,
        "short_name": node.short_name,
        "region": node.region,
        "assigned": node.assigned,
        "requests_count": len(node.requests),
        "new_requests": sum(1 for r in node.requests if r["created_in_period"]),
        "won": sum(1 for r in node.requests if r["closed_in_period"] == "won"),
        "lost": sum(1 for r in node.requests if r["closed_in_period"] == "lost"),
        "stuck": sum(1 for r in node.requests if r["is_stuck"]),
        "interactions": interactions,
        "last_contact_at": _iso(last_contact),
        "stages": _stage_chips(node.requests),
        "requests_truncated": not include_requests,
        "requests": [_public_request(r) for r in requests] if include_requests else [],
        # служебное для сводки (не сериализуется наружу)
        "_requests": requests,
        "_last_contact": last_contact,
    }


def _uni_sort_key(node: dict[str, Any]) -> tuple[Any, ...]:
    """«Проблемные сначала»: закреплён без контакта → больше зависших → давно не было."""
    total = node["interactions"]["total"]
    last = node["_last_contact"]
    return (
        node["university_id"] is None,  # узел B2C — в конце
        not (node["assigned"] and total == 0),
        -node["stuck"],
        last is not None,
        last or datetime.min.replace(tzinfo=timezone.utc),
        _name_key(node["name"]),
    )


def assemble_tree(
    deal_rows: Sequence[DealRow],
    activity_stats: Sequence[ActivityStat],
    assigned: Sequence[UniversityRef],
    people: Mapping[uuid.UUID, PersonRef],
    bounds: PeriodBounds,
    *,
    include_requests: bool = True,
) -> dict[str, Any]:
    """Дерево КАМ → вузы → заявки со свёртками и итогами (`totals`, `rows`)."""
    nodes: dict[uuid.UUID, dict[uuid.UUID | None, _UniNode]] = {}

    def node_for(
        kam_id: uuid.UUID,
        university_id: uuid.UUID | None,
        name: str,
        short_name: str | None,
        region: str | None,
    ) -> _UniNode:
        by_uni = nodes.setdefault(kam_id, {})
        node = by_uni.get(university_id)
        if node is None:
            node = by_uni[university_id] = _UniNode(
                university_id=university_id, name=name, short_name=short_name, region=region
            )
        return node

    for row in deal_rows:
        if row.pipeline == "b2b" and row.university_id is not None:
            node = node_for(
                row.responsible_id,
                row.university_id,
                row.university_name or "Вуз без названия",
                row.university_short,
                row.university_region,
            )
        else:
            node = node_for(row.responsible_id, None, B2C_NODE_NAME, None, None)
        node.requests.append(request_item(row, bounds))

    for ref in assigned:
        node_for(
            ref.kam_id, ref.university_id, ref.name, ref.short_name, ref.region
        ).assigned = True

    for stat in activity_stats:
        exists = stat.university_id in nodes.get(stat.responsible_id, {})
        if stat.activities <= 0 and not exists:
            continue  # только давние мероприятия без заявок и закрепления — строку не заводим
        node = node_for(
            stat.responsible_id, stat.university_id, stat.name, stat.short_name, stat.region
        )
        node.activities += stat.activities
        node.activity_last = _max_dt(node.activity_last, stat.last_at)

    person_ids = set(people) | set(nodes)
    rows: list[dict[str, Any]] = []
    for person_id in person_ids:
        person = people.get(person_id) or PersonRef(
            user_id=person_id, full_name="Пользователь не найден", is_active=False
        )
        universities = sorted(
            (
                _finalize_node(node, include_requests=include_requests)
                for node in nodes.get(person_id, {}).values()
            ),
            key=_uni_sort_key,
        )
        rows.append(_kam_row(person, universities))
    rows.sort(key=lambda row: _name_key(row["kam"]))
    return {"totals": _totals(rows), "rows": rows}


def _kam_row(person: PersonRef, universities: list[dict[str, Any]]) -> dict[str, Any]:
    interactions = _empty_interactions()
    for node in universities:
        _add(interactions, node["interactions"])
    real = [node for node in universities if node["university_id"] is not None]
    touched = [node for node in real if node["interactions"]["total"] > 0]
    assigned = [node for node in real if node["assigned"]]
    assigned_touched = [node for node in assigned if node["interactions"]["total"] > 0]
    requests = [request for node in universities for request in node["_requests"]]
    won = sum(1 for r in requests if r["closed_in_period"] == "won")
    lost = sum(1 for r in requests if r["closed_in_period"] == "lost")
    new = [r for r in requests if r["created_in_period"]]
    return {
        "kam_id": str(person.user_id),
        "kam": person.full_name,
        "is_active": person.is_active,
        "roles": list(person.roles),
        "universities_count": len(real),
        "universities_touched": len(touched),
        "universities_assigned": len(assigned),
        "assigned_touched": len(assigned_touched),
        "assigned_untouched": len(assigned) - len(assigned_touched),
        "coverage_pct": _pct(len(assigned_touched), len(assigned)),
        "requests_count": len(requests),
        "new_requests": len(new),
        "new_amount": _money(sum((r["_amount"] or Decimal(0) for r in new), Decimal(0))),
        "won": won,
        "lost": lost,
        "conversion": _conversion(won, lost),
        "stuck": sum(1 for r in requests if r["is_stuck"]),
        "interactions": interactions,
        "last_contact_at": _iso(_max_dt(*(node["_last_contact"] for node in universities))),
        "stages": _merge_chips(node["stages"] for node in universities),
        "universities": universities,
    }


def _totals(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    interactions = _empty_interactions()
    touched: set[str] = set()
    assigned: set[str] = set()
    universities: set[str] = set()
    requests = 0
    new_requests = 0
    new_amount = Decimal(0)
    won = lost = stuck = 0
    for row in rows:
        _add(interactions, row["interactions"])
        requests += row["requests_count"]
        new_requests += row["new_requests"]
        new_amount += Decimal(row["new_amount"] or 0)
        won += row["won"]
        lost += row["lost"]
        stuck += row["stuck"]
        for node in row["universities"]:
            uid = node["university_id"]
            if uid is None:
                continue
            universities.add(uid)
            if node["interactions"]["total"] > 0:
                touched.add(uid)
            if node["assigned"]:
                assigned.add(uid)
    assigned_touched = assigned & touched  # с вузом работал кто угодно из выборки
    return {
        "kams": len(rows),
        "universities_count": len(universities),
        "universities_touched": len(touched),
        "universities_assigned": len(assigned),
        "assigned_touched": len(assigned_touched),
        "coverage_pct": _pct(len(assigned_touched), len(assigned)),
        "requests_count": requests,
        "new_requests": new_requests,
        "new_amount": _money(new_amount),
        "won": won,
        "lost": lost,
        "conversion": _conversion(won, lost),
        "stuck": stuck,
        "interactions": interactions,
    }


def public_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """Дерево без служебных `_`-полей — то, что уходит в ответ API."""

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if not k.startswith("_")}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(tree)


# --------------------------------------------------------------------------------------
# Сводка: KPI с дельтами, этапы, динамика, выводы
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodKpi:
    coverage_pct: float | None
    universities_touched: int
    universities_assigned: int
    assigned_touched: int
    requests_count: int
    new_requests: int
    new_amount: Decimal
    won: int
    lost: int
    conversion: float | None
    interactions: int
    stuck: int
    talent_pool_added: int = 0


def summarize_period(tree: Mapping[str, Any], *, talent_pool_added: int = 0) -> PeriodKpi:
    totals = tree["totals"]
    return PeriodKpi(
        coverage_pct=totals["coverage_pct"],
        universities_touched=totals["universities_touched"],
        universities_assigned=totals["universities_assigned"],
        assigned_touched=totals["assigned_touched"],
        requests_count=totals["requests_count"],
        new_requests=totals["new_requests"],
        new_amount=Decimal(totals["new_amount"] or 0),
        won=totals["won"],
        lost=totals["lost"],
        conversion=totals["conversion"],
        interactions=totals["interactions"]["total"],
        stuck=totals["stuck"],
        talent_pool_added=talent_pool_added,
    )


def _delta(value: Any, prev: Any) -> Any:
    if value is None or prev is None:
        return None
    if isinstance(value, Decimal):
        return format(value - prev, "f")
    if isinstance(value, float) or isinstance(prev, float):
        return round(float(value) - float(prev), 1)
    return value - prev


def kpi_with_delta(cur: PeriodKpi, prev: PeriodKpi) -> dict[str, dict[str, Any]]:
    """`{"value","prev","delta"}` на метрику; проценты — дельта в п.п.; деньги — строкой."""
    result: dict[str, dict[str, Any]] = {}
    for name in PeriodKpi.__dataclass_fields__:
        value, previous = getattr(cur, name), getattr(prev, name)
        delta = _delta(value, previous)
        if isinstance(value, Decimal):
            value, previous = format(value, "f"), format(previous, "f")
        result[name] = {"value": value, "prev": previous, "delta": delta}
    return result


@dataclass(frozen=True)
class StageRef:
    stage_id: uuid.UUID
    workflow_type: str
    code: str
    name: str
    color: str | None
    sort_order: int
    is_terminal: bool
    outcome: str | None
    deleted: bool
    is_initial: bool = False
    threshold_days: int | None = None  # порог «зависания» каскадом (этап → воронка → общий)


@dataclass(frozen=True)
class StageStat:
    reached: int = 0
    avg_days: float | None = None
    intervals: int = 0


async def fetch_stages(
    session: AsyncSession, workflow_types: Sequence[str], *, default_days: int
) -> dict[str, list[StageRef]]:
    """Этапы воронок, включая мягко удалённые (нужны, если они есть в истории)."""
    if not workflow_types:
        return {}
    stmt = (
        select(WorkflowStage, Workflow.code, Workflow.stuck_threshold_days)
        .join(Workflow, Workflow.id == WorkflowStage.workflow_id)
        .where(Workflow.code.in_(tuple(workflow_types)))
        .order_by(Workflow.code, WorkflowStage.sort_order)
    )
    result: dict[str, list[StageRef]] = {wf: [] for wf in workflow_types}
    for stage, workflow_code, workflow_threshold in (await session.execute(stmt)).all():
        result.setdefault(workflow_code, []).append(
            StageRef(
                stage_id=stage.id,
                workflow_type=workflow_code,
                code=stage.code,
                name=stage.name,
                color=stage.color,
                sort_order=stage.sort_order,
                is_terminal=stage.is_terminal,
                outcome=stage.terminal_outcome,
                deleted=stage.deleted_at is not None,
                is_initial=stage.is_initial,
                threshold_days=stage.stuck_threshold_days or workflow_threshold or default_days,
            )
        )
    return result


def _deal_ids_param(deal_ids: Sequence[uuid.UUID]) -> sa.BindParameter[Any]:
    # ОДИН параметр-массив: без лимита asyncpg в 32 767 параметров
    return sa.bindparam(
        "deal_ids",
        value=list(deal_ids),
        type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
    )


def _ts(value: datetime) -> sa.BindParameter[Any]:
    return sa.literal(value, type_=sa.TIMESTAMP(timezone=True))


def stage_reached_stmt(deal_ids: Sequence[uuid.UUID], bounds: PeriodBounds) -> sa.Select:
    history = DealStageHistory
    return (
        select(history.to_stage_id, func.count(func.distinct(history.deal_id)))
        .where(
            history.deal_id == sa.any_(_deal_ids_param(deal_ids)),
            history.created_at < bounds.as_of,
        )
        .group_by(history.to_stage_id)
    )


def stage_dwell_stmt(deal_ids: Sequence[uuid.UUID], bounds: PeriodBounds) -> sa.Select:
    """Интервалы пребывания на этапах (оконная LEAD), пересекающиеся с периодом."""
    history = DealStageHistory
    intervals = (
        select(
            history.to_stage_id.label("stage_id"),
            history.created_at.label("entered"),
            func.lead(history.created_at)
            .over(partition_by=history.deal_id, order_by=(history.created_at, history.id))
            .label("left_at"),
        )
        .where(
            history.deal_id == sa.any_(_deal_ids_param(deal_ids)),
            history.created_at < bounds.as_of,
        )
        .subquery("intervals")
    )
    left = func.coalesce(intervals.c.left_at, _ts(bounds.as_of))
    days = func.extract("epoch", left - intervals.c.entered) / 86400.0
    return (
        select(intervals.c.stage_id, func.avg(days), func.count())
        .where(left > _ts(bounds.start))
        .group_by(intervals.c.stage_id)
    )


async def fetch_stage_stats(
    session: AsyncSession, deal_ids: Sequence[uuid.UUID], bounds: PeriodBounds
) -> dict[uuid.UUID, StageStat]:
    if not deal_ids:
        return {}
    reached = dict((await session.execute(stage_reached_stmt(deal_ids, bounds))).all())
    dwell = {
        stage_id: (avg, count)
        for stage_id, avg, count in (
            await session.execute(stage_dwell_stmt(deal_ids, bounds))
        ).all()
    }
    result: dict[uuid.UUID, StageStat] = {}
    for stage_id in set(reached) | set(dwell):
        avg, count = dwell.get(stage_id, (None, 0))
        result[stage_id] = StageStat(
            reached=int(reached.get(stage_id, 0)),
            avg_days=round(float(avg), 1) if avg is not None else None,
            intervals=int(count or 0),
        )
    return result


def _tree_requests(tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        request
        for row in tree["rows"]
        for node in row["universities"]
        for request in node["_requests"]
    ]


def stage_rows(
    stages_by_workflow: Mapping[str, Sequence[StageRef]],
    stage_stats: Mapping[uuid.UUID, StageStat],
    tree: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Конверсия по этапам: дошли, доля от входа, конверсия шага, сейчас на этапе, ср. дней.

    Нетерминальные этапы — по порядку схемы, терминальные (выигрыш, затем отказ) — в
    конце. Мягко удалённый этап попадает в список, только если он есть в истории.
    """
    on_stage: dict[str, int] = {}
    stuck: dict[str, int] = {}
    for request in _tree_requests(tree):
        stage_id = request["stage"]["id"]
        on_stage[stage_id] = on_stage.get(stage_id, 0) + 1
        if request["is_stuck"]:
            stuck[stage_id] = stuck.get(stage_id, 0) + 1

    result: dict[str, list[dict[str, Any]]] = {}
    for workflow_type, stages in stages_by_workflow.items():
        visible = [
            stage
            for stage in stages
            if not stage.deleted
            or stage_stats.get(stage.stage_id, StageStat()).reached > 0
            or on_stage.get(str(stage.stage_id), 0) > 0
        ]
        active = sorted((s for s in visible if not s.is_terminal), key=lambda s: s.sort_order)
        terminal = sorted(
            (s for s in visible if s.is_terminal),
            key=lambda s: (s.outcome != "won", s.sort_order),
        )
        first = next((s for s in active if s.is_initial and not s.deleted), None) or (
            active[0] if active else None
        )
        first_reached = stage_stats.get(first.stage_id, StageStat()).reached if first else 0
        last_active_reached: int | None = None
        rows: list[dict[str, Any]] = []
        previous_reached: int | None = None
        for stage in active + terminal:
            stat = stage_stats.get(stage.stage_id, StageStat())
            if not stage.is_terminal:
                step = (
                    _pct(stat.reached, previous_reached) if previous_reached is not None else None
                )
                previous_reached = stat.reached
                last_active_reached = stat.reached
            elif stage.outcome == "won" and last_active_reached is not None:
                step = _pct(stat.reached, last_active_reached)
            else:
                step = None
            rows.append(
                {
                    "id": str(stage.stage_id),
                    "code": stage.code,
                    "name": f"{stage.name}{DELETED_SUFFIX}" if stage.deleted else stage.name,
                    "color": stage.color,
                    "is_terminal": stage.is_terminal,
                    "outcome": stage.outcome,
                    "deleted": stage.deleted,
                    "reached": stat.reached,
                    "share_of_first_pct": _pct(stat.reached, first_reached),
                    "step_conversion_pct": step,
                    "on_stage_at_end": on_stage.get(str(stage.stage_id), 0),
                    "stuck": stuck.get(str(stage.stage_id), 0),
                    "avg_days": None if stage.is_terminal else stat.avg_days,
                    "intervals": 0 if stage.is_terminal else stat.intervals,
                    "threshold_days": None if stage.is_terminal else stage.threshold_days,
                }
            )
        result[workflow_type] = rows
    return result


def _bucket_start(day: date, granularity: str) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    return day


def _next_bucket(day: date, granularity: str) -> date:
    if granularity == "week":
        return day + timedelta(weeks=1)
    if granularity == "month":
        return (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return day + timedelta(days=1)


def fill_buckets(bounds: PeriodBounds, granularity: str) -> list[date]:
    """Непрерывная сетка бакетов периода (как report_dynamics), недостающие = 0."""
    if granularity not in _GRANULARITIES:
        raise ValueError(f"неизвестная гранулярность {granularity!r}")
    cursor = _bucket_start(bounds.date_from, granularity)
    last = _bucket_start(bounds.date_to, granularity)
    buckets: list[date] = []
    while cursor <= last:
        buckets.append(cursor)
        cursor = _next_bucket(cursor, granularity)
    return buckets


def _bucket_expr(column: Any, granularity: str) -> Any:
    # единица и зона — литералы (whitelist), НЕ bind-параметры: иначе одно и то же
    # выражение в SELECT и GROUP BY рендерится разными $N (ловушка GroupingError)
    if granularity not in _GRANULARITIES:
        raise ValueError(f"неизвестная гранулярность {granularity!r}")
    return func.date_trunc(
        sa.literal_column(f"'{granularity}'"),
        func.timezone(sa.literal_column("'Europe/Moscow'"), column),
    ).label("bucket")


def dynamics_stmt(
    deal_ids: Sequence[uuid.UUID],
    params: KamActivityParams,
    bounds: PeriodBounds,
    granularity: str,
    *,
    activities_visible: bool,
    activities_own: uuid.UUID | None = None,
) -> sa.Select:
    """Один UNION ALL событий (kind, bucket) — собственные действия ответственного КАМа."""
    ids = _deal_ids_param(deal_ids)
    history = DealStageHistory
    transitions = (
        select(
            sa.literal_column("'transitions'").label("kind"),
            _bucket_expr(history.created_at, granularity),
        )
        .select_from(history)
        .join(Deal, Deal.id == history.deal_id)
        .where(
            history.deal_id == sa.any_(ids),
            history.kind == "manual",
            history.from_stage_id.is_not(None),
            history.actor_user_id == Deal.responsible_user_id,
            history.created_at >= bounds.start,
            history.created_at < bounds.as_of,
        )
    )
    comments = (
        select(
            sa.literal_column("'comments'").label("kind"),
            _bucket_expr(DealComment.created_at, granularity),
        )
        .select_from(DealComment)
        .join(Deal, Deal.id == DealComment.deal_id)
        .where(
            DealComment.deal_id == sa.any_(ids),
            DealComment.author_user_id == Deal.responsible_user_id,
            DealComment.deleted_at.is_(None),
            DealComment.created_at >= bounds.start,
            DealComment.created_at < bounds.as_of,
        )
    )
    files = (
        select(
            sa.literal_column("'files'").label("kind"),
            _bucket_expr(FileObject.created_at, granularity),
        )
        .select_from(FileObject)
        .join(Deal, Deal.id == FileObject.entity_id)
        .where(
            FileObject.entity_type == "deal",
            FileObject.entity_id == sa.any_(ids),
            FileObject.uploaded_by == Deal.responsible_user_id,
            FileObject.deleted_at.is_(None),
            FileObject.created_at >= bounds.start,
            FileObject.created_at < bounds.as_of,
        )
    )
    parts: list[sa.Select] = [transitions, comments, files]
    if activities_visible:
        activities = (
            select(
                sa.literal_column("'activities'").label("kind"),
                _bucket_expr(ActivitySchedule.starts_at, granularity),
            )
            .select_from(ActivitySchedule)
            .join(Activity, Activity.id == ActivitySchedule.activity_id)
            .where(
                Activity.university_id.is_not(None),
                Activity.responsible_user_id.is_not(None),
                ActivitySchedule.starts_at >= bounds.start,
                ActivitySchedule.starts_at < bounds.as_of,
            )
        )
        if params.kam_ids:
            activities = activities.where(Activity.responsible_user_id.in_(params.kam_ids))
        if params.university_ids:
            activities = activities.where(Activity.university_id.in_(params.university_ids))
        if activities_own is not None:
            activities = activities.where(Activity.responsible_user_id == activities_own)
        parts.append(activities)
    events = sa.union_all(*parts).subquery("events")
    return select(events.c.kind, events.c.bucket, func.count()).group_by(
        events.c.kind, events.c.bucket
    )


async def fetch_dynamics(
    session: AsyncSession,
    deal_ids: Sequence[uuid.UUID],
    params: KamActivityParams,
    bounds: PeriodBounds,
    granularity: str,
    scope: DealScope,
) -> dict[str, dict[date, int]]:
    visible, own = _b2b_rule(params, scope)
    stmt = dynamics_stmt(
        deal_ids, params, bounds, granularity, activities_visible=visible, activities_own=own
    )
    result: dict[str, dict[date, int]] = {}
    for kind, bucket, count in (await session.execute(stmt)).all():
        day = bucket.date() if isinstance(bucket, datetime) else bucket
        result.setdefault(kind, {})[day] = int(count)
    return result


def dynamics_series(
    events: Mapping[str, Mapping[date, int]],
    tree: Mapping[str, Any],
    bounds: PeriodBounds,
    granularity: str,
) -> list[dict[str, Any]]:
    buckets = fill_buckets(bounds, granularity)
    new_requests: dict[date, int] = {}
    for request in _tree_requests(tree):
        if request["created_in_period"]:
            day = request["_created_at"].astimezone(REPORT_TZ).date()
            key = _bucket_start(day, granularity)
            new_requests[key] = new_requests.get(key, 0) + 1
    series = []
    for key in ("transitions", "comments", "files", "activities"):
        points = events.get(key, {})
        series.append(
            {
                "key": key,
                "label": INTERACTION_LABELS[key],
                "points": [
                    {"date": bucket.isoformat(), "value": int(points.get(bucket, 0))}
                    for bucket in buckets
                ],
            }
        )
    series.append(
        {
            "key": "new_requests",
            "label": INTERACTION_LABELS["new_requests"],
            "points": [
                {"date": bucket.isoformat(), "value": new_requests.get(bucket, 0)}
                for bucket in buckets
            ],
        }
    )
    return series


def _university_label(node: Mapping[str, Any]) -> str:
    return node.get("short_name") or node["name"]


def top_stuck(tree: Mapping[str, Any], limit: int = TOP_STUCK_LIMIT) -> list[dict[str, Any]]:
    items = []
    for row in tree["rows"]:
        for node in row["universities"]:
            for request in node["_requests"]:
                if not request["is_stuck"]:
                    continue
                items.append(
                    {
                        "request_id": request["request_id"],
                        "title": request["title"],
                        "workflow_type": request["workflow_type"],
                        "university": request["client"]
                        if node["university_id"] is None
                        else _university_label(node),
                        "kam_id": row["kam_id"],
                        "kam": row["kam"],
                        "stage": {
                            key: request["stage"][key]
                            for key in ("id", "code", "name", "color", "is_terminal", "outcome")
                        },
                        "days_on_stage": request["days_on_stage"],
                        "threshold_days": request["threshold_days"],
                        "overdue_days": request["days_on_stage"] - request["threshold_days"],
                    }
                )
    items.sort(key=lambda item: (-item["days_on_stage"], -item["overdue_days"], item["title"]))
    return items[:limit]


def by_kam_rows(tree: Mapping[str, Any], prev_tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    previous = {row["kam_id"]: row for row in prev_tree["rows"]}
    rows = []
    for row in tree["rows"]:
        prev = previous.get(row["kam_id"])
        rows.append(
            {
                "kam_id": row["kam_id"],
                "kam": row["kam"],
                "is_active": row["is_active"],
                "universities_touched": row["universities_touched"],
                "universities_assigned": row["universities_assigned"],
                "assigned_touched": row["assigned_touched"],
                "coverage_pct": row["coverage_pct"],
                "requests_count": row["requests_count"],
                "new_requests": row["new_requests"],
                "won": row["won"],
                "lost": row["lost"],
                "conversion": row["conversion"],
                "interactions": row["interactions"]["total"],
                "interactions_prev": prev["interactions"]["total"] if prev else 0,
                "interactions_detail": row["interactions"],
                "stuck": row["stuck"],
                "last_contact_at": row["last_contact_at"],
            }
        )
    rows.sort(key=lambda item: (-item["interactions"], _name_key(item["kam"])))
    return rows


def untouched_assigned(tree: Mapping[str, Any]) -> list[str]:
    """Закреплённые вузы без единого взаимодействия в периоде («забытые»)."""
    touched = {
        node["university_id"]
        for row in tree["rows"]
        for node in row["universities"]
        if node["university_id"] and node["interactions"]["total"] > 0
    }
    names: dict[str, str] = {}
    for row in tree["rows"]:
        for node in row["universities"]:
            uid = node["university_id"]
            if uid and node["assigned"] and uid not in touched:
                names[uid] = _university_label(node)
    return sorted(names.values(), key=_name_key)


# --- склонения и форматирование выводов -----------------------------------------------


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def _signed(value: float | int | None, unit: str = "") -> str:
    if value is None:
        return "—"
    if value == 0:
        return f"±0{unit}"
    number = f"{abs(value):g}".replace(".", ",")
    return f"{'+' if value > 0 else '−'}{number}{unit}"


def _num(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}".replace(".", ",")
    return f"{int(value):,}".replace(",", " ")


def _rub(value: Decimal | str | None) -> str:
    amount = Decimal(value or 0)
    return f"{int(amount.quantize(Decimal(1))):,}".replace(",", " ") + " ₽"


def build_highlights(summary: Mapping[str, Any]) -> list[str]:
    """≤5 детерминированных фраз для заказчика (без ИИ): тот же текст в UI и PDF."""
    kpi = summary["kpi"]
    lines: list[str] = []

    assigned = kpi["universities_assigned"]["value"]
    if assigned:
        touched = kpi["assigned_touched"]["value"]
        # после «из N» — родительный падеж: «из 1 закреплённого вуза», «из 5 закреплённых вузов»
        noun = _plural(assigned, ("закреплённого вуза", "закреплённых вузов", "закреплённых вузов"))
        lines.append(
            f"Работали с {touched} из {assigned} {noun} "
            f"({_num(kpi['coverage_pct']['value'])}%), "
            f"к прошлому периоду {_signed(kpi['assigned_touched']['delta'])}."
        )

    new = kpi["new_requests"]["value"]
    if new:
        lines.append(
            f"Новых заявок: {new} ({_signed(kpi['new_requests']['delta'])}) "
            f"на сумму {_rub(kpi['new_amount']['value'])}."
        )
    else:
        lines.append("Новых заявок за период нет.")

    won, lost = kpi["won"]["value"], kpi["lost"]["value"]
    if won or lost:
        conversion = kpi["conversion"]
        delta = (
            f" ({_signed(conversion['delta'], ' п.п.')})" if conversion["delta"] is not None else ""
        )
        lines.append(
            f"Выиграно {won}, отказов {lost}, конверсия {_num(conversion['value'])}%{delta}."
        )
    else:
        lines.append("Закрытых заявок за период нет.")

    bottleneck = None
    for rows in summary.get("stages", {}).values():
        for stage in rows:
            if stage["is_terminal"] or stage["avg_days"] is None or stage["intervals"] < 2:
                continue
            if bottleneck is None or stage["avg_days"] > bottleneck["avg_days"]:
                bottleneck = stage
    if bottleneck is not None:
        lines.append(
            f"Дольше всего заявки задерживаются на этапе «{bottleneck['name']}»: "
            f"в среднем {_num(bottleneck['avg_days'])} дн., сейчас зависли {bottleneck['stuck']}."
        )

    untouched = list(summary.get("untouched_assigned", []))
    if untouched:
        shown = ", ".join(untouched[:3])
        rest = len(untouched) - 3
        tail = f" (и ещё {rest})" if rest > 0 else ""
        lines.append(f"Без взаимодействий остались закреплённые вузы: {shown}{tail}.")

    talent = summary.get("talent") or {}
    added = (talent.get("talent_pool_added") or {}).get("value")
    if added and len(lines) < HIGHLIGHTS_LIMIT:
        lines.append(
            f"В пул талантов за период {_plural(added, ('добавлен', 'добавлено', 'добавлено'))} "
            f"{added} {_plural(added, ('студент', 'студента', 'студентов'))}."
        )
    return lines[:HIGHLIGHTS_LIMIT]


# --------------------------------------------------------------------------------------
# Студенты на программах из пула талантов (сводка для заказчика)
# --------------------------------------------------------------------------------------


def _talent_condition(
    params: KamActivityParams,
    scope: DealScope,
    university_ids: set[uuid.UUID],
    counterparty_ids: set[uuid.UUID],
) -> sa.ColumnElement[bool] | None:
    """Студенты вузов отчёта (B2B) и B2C-клиентов отчёта; без фильтров — все."""
    workflows = _visible_workflows(params, scope)
    wide = _is_wide(params, scope)
    if wide and set(workflows) == set(WORKFLOW_TYPES):
        return sa.true()
    parts: list[sa.ColumnElement[bool]] = []
    if "b2b" in workflows:
        if wide:
            parts.append(Student.university_id.is_not(None))
        elif university_ids:
            parts.append(
                Student.university_id
                == sa.any_(
                    sa.bindparam(
                        "talent_universities",
                        value=sorted(university_ids, key=str),
                        type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                    )
                )
            )
    if "b2c" in workflows:
        if wide:
            parts.append(Student.counterparty_id.is_not(None))
        elif counterparty_ids:
            parts.append(
                Student.counterparty_id
                == sa.any_(
                    sa.bindparam(
                        "talent_clients",
                        value=sorted(counterparty_ids, key=str),
                        type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                    )
                )
            )
    return or_(*parts) if parts else None


def _empty_talent() -> dict[str, Any]:
    return {
        "students_total": 0,
        "on_programs": 0,
        "by_status": [
            {
                "key": status,
                "label": FUNNEL_LABELS[status],
                "color": FUNNEL_COLORS[status],
                "value": 0,
                "entered": 0,
            }
            for status in FUNNEL_ORDER
        ],
        "talent_pool_added": {"value": 0, "prev": 0, "delta": 0},
        "programs": [],
    }


async def fetch_talent(
    session: AsyncSession,
    params: KamActivityParams,
    scope: DealScope,
    bounds: PeriodBounds,
    prev: PeriodBounds,
    *,
    university_ids: set[uuid.UUID],
    counterparty_ids: set[uuid.UUID],
) -> dict[str, Any]:
    """Воронка студентов на `as_of` (по истории статусов) и программы, где они учатся.

    Только агрегаты: ПДн студентов (шифртекст) не читаются вовсе.
    """
    condition = _talent_condition(params, scope, university_ids, counterparty_ids)
    if condition is None:
        return _empty_talent()
    as_of = bounds.as_of
    history = aliased(StudentStatusHistory, name="ssh_last")
    last = (
        select(history.to_status.label("to_status"))
        .where(history.student_id == Student.id, history.created_at < as_of)
        .order_by(history.created_at.desc(), history.id.desc())
        .limit(1)
        .lateral("last_status")
    )
    program_university = aliased(University, name="program_university")
    status_at = func.coalesce(last.c.to_status, Student.funnel_status).label("funnel_at")
    stmt = (
        select(
            Student.program_id,
            Program.name,
            program_university.short_name,
            program_university.name,
            status_at,
            func.count(),
        )
        .select_from(Student)
        .outerjoin(last, sa.true())
        .outerjoin(Program, Program.id == Student.program_id)
        .outerjoin(program_university, program_university.id == Program.university_id)
        .where(condition, or_(last.c.to_status.is_not(None), Student.created_at < as_of))
        .group_by(
            Student.program_id,
            Program.name,
            program_university.short_name,
            program_university.name,
            sa.text("funnel_at"),
        )
    )
    by_status: dict[str, int] = dict.fromkeys(FUNNEL_ORDER, 0)
    programs: dict[str, dict[str, Any]] = {}
    for program_id, program_name, uni_short, uni_name, status, count in (
        await session.execute(stmt)
    ).all():
        count = int(count)
        if status in by_status:
            by_status[status] += count
        if program_id is None:
            continue
        item = programs.setdefault(
            str(program_id),
            {
                "program_id": str(program_id),
                "name": program_name,
                "university": uni_short or uni_name,
                **dict.fromkeys(FUNNEL_ORDER, 0),
                "total": 0,
            },
        )
        if status in FUNNEL_ORDER:
            item[status] += count
        item["total"] += count

    entered_history = aliased(StudentStatusHistory, name="ssh_entered")
    in_cur = and_(entered_history.created_at >= bounds.start, entered_history.created_at < as_of)
    in_prev = and_(
        entered_history.created_at >= prev.start, entered_history.created_at < prev.as_of
    )
    entered_stmt = (
        select(
            entered_history.to_status,
            func.count().filter(in_cur),
            func.count().filter(in_prev),
        )
        .join(Student, Student.id == entered_history.student_id)
        .where(condition, entered_history.created_at >= prev.start)
        .group_by(entered_history.to_status)
    )
    entered: dict[str, tuple[int, int]] = {
        status: (int(cur or 0), int(previous or 0))
        for status, cur, previous in (await session.execute(entered_stmt)).all()
    }
    added_cur, added_prev = entered.get("talent_pool", (0, 0))
    top = sorted(
        programs.values(),
        key=lambda item: (
            -(item["studying"] + item["graduate"] + item["talent_pool"]),
            -item["total"],
            _name_key(item["name"] or ""),
        ),
    )[:TOP_PROGRAMS_LIMIT]
    return {
        "students_total": sum(by_status.values()),
        "on_programs": sum(
            item["studying"] + item["graduate"] + item["talent_pool"] for item in programs.values()
        ),
        "by_status": [
            {
                "key": status,
                "label": FUNNEL_LABELS[status],
                "color": FUNNEL_COLORS[status],
                "value": by_status[status],
                "entered": entered.get(status, (0, 0))[0],
            }
            for status in FUNNEL_ORDER
        ],
        "talent_pool_added": {
            "value": added_cur,
            "prev": added_prev,
            "delta": added_cur - added_prev,
        },
        "programs": top,
    }


# --------------------------------------------------------------------------------------
# Точки входа (кэш и конверт)
# --------------------------------------------------------------------------------------


def _scope_payload(scope: DealScope) -> dict[str, Any]:
    own = scope.own_user_id
    return {
        "own_only": own is not None,
        "own_user_id": str(own) if own else None,
        "workflow_types": list(scope.workflow_types),
    }


def _prepare(
    viewer: AppUser, params: KamActivityParams, scope: DealScope | None
) -> tuple[KamActivityParams, DealScope, PeriodBounds]:
    now = _now()
    params = resolve_params(params, today=now.astimezone(REPORT_TZ).date())
    scope = scope if scope is not None else viewer_scope(viewer)
    params = _resolve_scope(viewer, params, scope)
    return params, scope, resolve_bounds(params, now=now)


async def _collect(
    session: AsyncSession,
    params: KamActivityParams,
    bounds: PeriodBounds,
    scope: DealScope,
    *,
    default_days: int,
    assigned: Sequence[UniversityRef] | None = None,
) -> tuple[list[DealRow], list[ActivityStat], list[UniversityRef]]:
    rows = await fetch_deal_rows(session, params, bounds, scope, default_days=default_days)
    activities = await fetch_activity_stats(session, params, bounds, scope)
    if assigned is None:
        assigned = await fetch_assigned_universities(session, params, scope)
    return rows, activities, list(assigned)


def _people_ids(
    rows: Iterable[DealRow], activities: Iterable[ActivityStat], assigned: Iterable[UniversityRef]
) -> set[uuid.UUID]:
    ids = {row.responsible_id for row in rows}
    ids |= {ref.kam_id for ref in assigned}
    ids |= {stat.responsible_id for stat in activities if stat.activities > 0}
    return ids


async def kam_activity_detail(
    session: AsyncSession,
    params: KamActivityParams,
    bounds: PeriodBounds,
    scope: DealScope,
    *,
    force_requests: bool = False,
) -> dict[str, Any]:
    """Тело детализации (без конверта и кэша): `period`, `scope`, `totals`, `rows`."""
    default_days = await _global_stuck_default(session)
    rows, activities, assigned = await _collect(
        session, params, bounds, scope, default_days=default_days
    )
    people = await fetch_people(session, _people_ids(rows, activities, assigned), params, scope)
    include_requests = force_requests or len(rows) <= MAX_DETAIL_REQUESTS
    tree = assemble_tree(
        rows, activities, assigned, people, bounds, include_requests=include_requests
    )
    return {
        "period": bounds.as_json(),
        "scope": _scope_payload(scope),
        **public_tree(tree),
    }


async def kam_activity_summary(
    session: AsyncSession, params: KamActivityParams, bounds: PeriodBounds, scope: DealScope
) -> dict[str, Any]:
    """Тело сводки (без конверта и кэша)."""
    default_days = await _global_stuck_default(session)
    prev = previous_bounds(bounds)
    rows, activities, assigned = await _collect(
        session, params, bounds, scope, default_days=default_days
    )
    prev_rows, prev_activities, _ = await _collect(
        session, params, prev, scope, default_days=default_days, assigned=assigned
    )
    people = await fetch_people(
        session,
        _people_ids(rows, activities, assigned) | _people_ids(prev_rows, prev_activities, ()),
        params,
        scope,
    )
    tree = assemble_tree(rows, activities, assigned, people, bounds)
    prev_tree = assemble_tree(prev_rows, prev_activities, assigned, people, prev)

    university_ids = {
        uuid.UUID(node["university_id"])
        for row in tree["rows"]
        for node in row["universities"]
        if node["university_id"]
    }
    counterparty_ids = {row.counterparty_id for row in rows if row.counterparty_id}
    talent = await fetch_talent(
        session,
        params,
        scope,
        bounds,
        prev,
        university_ids=university_ids,
        counterparty_ids=counterparty_ids,
    )
    added = talent["talent_pool_added"]
    kpi = kpi_with_delta(
        summarize_period(tree, talent_pool_added=added["value"]),
        summarize_period(prev_tree, talent_pool_added=added["prev"]),
    )

    deal_ids = [row.deal_id for row in rows]
    workflows = _visible_workflows(params, scope)
    stages = stage_rows(
        await fetch_stages(session, workflows, default_days=default_days),
        await fetch_stage_stats(session, deal_ids, bounds),
        tree,
    )
    granularity = pick_granularity(bounds.days)
    events = await fetch_dynamics(session, deal_ids, params, bounds, granularity, scope)

    body: dict[str, Any] = {
        "period": {**bounds.as_json(), "granularity": granularity},
        "previous_period": prev.as_json(),
        "scope": _scope_payload(scope),
        "kpi": kpi,
        "stages": stages,
        "dynamics": {
            "granularity": granularity,
            "series": dynamics_series(events, tree, bounds, granularity),
        },
        "by_kam": by_kam_rows(tree, prev_tree),
        "top_stuck": top_stuck(tree),
        "untouched_assigned": untouched_assigned(tree),
        "talent": talent,
    }
    body["highlights"] = build_highlights(body)
    return body


async def build_kam_activity(
    session: AsyncSession,
    viewer: AppUser,
    params: KamActivityParams,
    *,
    scope: DealScope | None = None,
    force_requests: bool = False,
) -> dict[str, Any]:
    """Детализация (А) с кэшем cache-aside (TTL 60 с). `force_requests` (выгрузка) —
    все заявки без предохранителя размера, мимо кэша."""
    params, scope, bounds = _prepare(viewer, params, scope)
    if force_requests:
        body = await kam_activity_detail(session, params, bounds, scope, force_requests=True)
        return _envelope(CODE_DETAIL, params, body)  # type: ignore[arg-type]
    cache = get_cache()
    key = _cache_key(CODE_DETAIL, params, scope)
    cached = await cache.get_json(key)
    if cached is not None:
        return cached
    body = await kam_activity_detail(session, params, bounds, scope)
    result = _envelope(CODE_DETAIL, params, body)  # type: ignore[arg-type]
    await cache.set_json(key, result, REPORT_CACHE_TTL_SECONDS)
    return result


async def build_kam_activity_summary(
    session: AsyncSession,
    viewer: AppUser,
    params: KamActivityParams,
    *,
    scope: DealScope | None = None,
) -> dict[str, Any]:
    """Сводка для заказчика (Б) с кэшем cache-aside (TTL 60 с)."""
    params, scope, bounds = _prepare(viewer, params, scope)
    cache = get_cache()
    key = _cache_key(CODE_SUMMARY, params, scope)
    cached = await cache.get_json(key)
    if cached is not None:
        return cached
    body = await kam_activity_summary(session, params, bounds, scope)
    result = _envelope(CODE_SUMMARY, params, body)  # type: ignore[arg-type]
    await cache.set_json(key, result, REPORT_CACHE_TTL_SECONDS)
    return result
