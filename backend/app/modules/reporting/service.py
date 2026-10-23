"""JSON-агрегаты отчётов для дашбордов ECharts (api-contract.md §7.2, ux.md §6).

Единый формат: `{"report", "generated_at", "params", "series"|"rows"+"columns"}`.
Кэш — KeyDB `crm:v1:dash:{code}:{hash(params + visibility_scope)}`, TTL 60 с
(data-model.md §10); PNG/PDF графиков — обязанность фронта (`getDataURL`).
Зона видимости — `report_scope(viewer)` (`DealScope`, app.core.access): процессы ролей
с правом `reports:read`; в процессе без `reports:all` (или с ролью «только свои») —
только заявки, где пользователь ответственный. Для базовых ролей: kam — свои,
admin/head_kam/observer — все (§7.2, §12.2). Любой запрос по `deal` проходит через
`scope.apply(stmt)`; ключ кэша включает `scope.cache_key()`.

Единый слой фильтров (замечания к «Аналитике»): процесс, КАМы, вузы, ИТ-направления, продукты
и программы — списки id, условие `IN`, одинаково во всех секциях дашборда (KPI, этапы,
динамика, нагрузка КАМов, программы, студенты). ИТ-направление записи — направление её
программы, без него — направления её ИТ-продукта (`crm.directions.direction_match`, EXISTS
без размножения строк; то же правило у реестра программ и отчётов КАМов). Где измерение
к секции неприменимо, оно переводится явно (студенты по КАМу — студенты вузов, закреплённых
за КАМом) или не применяется, и это написано в docstring секции. Границы периода — сутки
по МСК, как в «Активности КАМов».
«Завершено» — вход в терминальный этап с исходом `won` по истории переходов (D16), одно
определение в KPI и в динамике; «Отказ» (`lost`) — отдельно.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import orjson
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.access import DealScope, get_catalog_snapshot
from app.core.cache import get_cache
from app.core.errors import validation_error
from app.core.rbac_catalog import WORKFLOW_TYPES
from app.models.crm import Contract, Program, University, UniversityContact
from app.models.deal import Deal, DealStageHistory
from app.models.talent import Student, StudentStatusHistory
from app.models.user import AppUser
from app.models.workflow import Workflow, WorkflowStage
from app.modules.crm.directions import direction_match
from app.modules.workflow.service import _global_stuck_default  # каскад порога (Р-5)

REPORT_CACHE_TTL_SECONDS = 60

# фиксированный сдвиг без DST — как REPORT_TZ отчёта «Активность КАМов» (D7)
REPORT_TZ = timezone(timedelta(hours=3), "MSK")
DEFAULT_KPI_DAYS = 30  # KPI без периода: последние 30 дней по МСК включительно
DEFAULT_DYNAMICS_WEEKS = 12
MAX_FILTER_IDS = 50  # как у отчёта «Активность КАМов»
PROGRAMS_DEMAND_CAP = 500  # предохранитель размера ответа (после сортировки, не до)
STUCK_ROWS_LIMIT = 500
TOP_UNIVERSITIES_PER_KAM = 5

REPORT_CODES = (
    "funnel",
    "kam-workload",
    "stuck",
    "universities-coverage",
    "programs-demand",
    "talent-pool-funnel",
    "dynamics",
    "dashboard",
)

WORKFLOW_LABELS = {"b2b": "B2B", "b2c": "B2C"}

FUNNEL_LABELS = {
    "candidate": "Кандидат",
    "studying": "Обучается",
    "graduate": "Выпускник",
    "talent_pool": "Пул талантов",
}
# фиолетовая гамма воронки студентов (ux.md §6.1, виджет 6)
FUNNEL_COLORS = {
    "candidate": "#d3adf7",
    "studying": "#b37feb",
    "graduate": "#9254de",
    "talent_pool": "#722ed1",
}

# этап «в обучении»: вход в него передаёт заявку в LMS (флаг конструктора) или код сида
LEARNING_STAGE_CODE = "in_learning"
STUDYING_STATUS = "studying"

DEMAND_FORMULA = (
    "Индекс 0–100: среднее трёх долей от максимума среди показанных программ — "
    "заявки на обучение, обучающиеся, параллельные потоки"
)

# id-фильтры: имя поля ReportParams = имя query-параметра `/reports/*`
ID_FILTERS = ("kam_id", "product_id", "university_id", "program_id", "direction_id")


# --------------------------------------------------------------------------------------
# Параметры
# --------------------------------------------------------------------------------------


def _invalid(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _parse_ids(
    value: Any, field: str, errors: list[dict[str, str]] | None = None
) -> tuple[uuid.UUID, ...]:
    """Скаляр, строка или список → отсортированный кортеж уникальных UUID.

    Принимает и `uuid.UUID` (query `/reports/*`), и строки (`filters` выгрузки). Без
    `errors` ошибка бросается сразу (400 validation_error), иначе копится в список.
    """
    if value is None or value == "":
        return ()
    items: Iterable[Any] = (
        [value] if isinstance(value, (uuid.UUID, str)) or not isinstance(value, Iterable) else value
    )
    parsed: set[uuid.UUID] = set()
    for item in items:
        if item is None or item == "":
            continue
        if isinstance(item, uuid.UUID):
            parsed.add(item)
            continue
        try:
            parsed.add(uuid.UUID(str(item)))
        except ValueError:
            problem = _invalid(field, "invalid_uuid", f"«{item}» — не идентификатор (UUID)")
            if errors is None:
                raise validation_error("Ошибка валидации данных", details=[problem]) from None
            errors.append(problem)
    if len(parsed) > MAX_FILTER_IDS:
        problem = _invalid(field, "too_many_values", f"Не больше {MAX_FILTER_IDS} значений фильтра")
        if errors is None:
            raise validation_error("Ошибка валидации данных", details=[problem])
        errors.append(problem)
    return tuple(sorted(parsed, key=str))


def _parse_date(value: Any, field: str, errors: list[dict[str, str]]) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(_invalid(field, "invalid_date", "Дата — в формате ГГГГ-ММ-ДД"))
        return None


@dataclass(frozen=True)
class ReportParams:
    """Фильтры панели дашборда; неиспользуемые отчётом поля игнорируются.

    `kam_id`/`product_id`/`university_id`/`program_id`/`direction_id` — кортежи id
    (условие `IN`): конструктор принимает и один UUID, и строку, и список (прежние вызовы
    `ReportParams(kam_id=uuid)` работают), нормализует и проверяет (400). Читать —
    через `kam_ids`/`product_ids`/`university_ids`/`program_ids`/`direction_ids`.
    """

    workflow_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    kam_id: Any = ()
    product_id: Any = ()
    university_id: Any = ()
    program_id: Any = ()
    direction_id: Any = ()  # ИТ-направления (каталог it_direction)
    region: str | None = None
    federal_project_id: uuid.UUID | None = None
    threshold_days: int | None = None

    def __post_init__(self) -> None:
        for name in ID_FILTERS:
            object.__setattr__(self, name, _parse_ids(getattr(self, name), name))
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise validation_error(
                "Ошибка валидации данных",
                details=[_invalid("from", "period_inverted", "Начало периода позже окончания")],
            )

    @property
    def kam_ids(self) -> tuple[uuid.UUID, ...]:
        return self.kam_id

    @property
    def product_ids(self) -> tuple[uuid.UUID, ...]:
        return self.product_id

    @property
    def university_ids(self) -> tuple[uuid.UUID, ...]:
        return self.university_id

    @property
    def program_ids(self) -> tuple[uuid.UUID, ...]:
        return self.program_id

    @property
    def direction_ids(self) -> tuple[uuid.UUID, ...]:
        return self.direction_id

    @property
    def has_period(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        result: dict[str, Any] = {}
        for key, value in payload.items():
            name = key.replace("date_from", "from").replace("date_to", "to")
            if key in ID_FILTERS:
                result[name] = [str(item) for item in value]
            else:
                result[name] = str(value) if value is not None else None
        if not result["direction_id"]:
            # фильтр появился позже остальных: без значения не попадает в ключ кэша и эхо
            # параметров — прежние ответы и ключи не меняются
            del result["direction_id"]
        return result

    @classmethod
    def from_filters(cls, filters: Mapping[str, Any]) -> ReportParams:
        """Параметры отчёта из `filters` задания выгрузки (`POST /export/jobs`,
        `report:<code>`): те же ключи, что у query `/reports/*` (`from`, `to`,
        `workflow_type`, `kam_id`, …, `federal_project`); id — строкой или списком, как
        их шлёт дашборд. Любая ошибка значения — 400 validation_error со списком полей."""
        errors: list[dict[str, str]] = []
        workflow_type = filters.get("workflow_type") or None
        if workflow_type is not None and workflow_type not in WORKFLOW_TYPES:
            errors.append(_invalid("workflow_type", "invalid_value", "Тип процесса — b2b или b2c"))
            workflow_type = None
        date_from = _parse_date(filters.get("from"), "from", errors)
        date_to = _parse_date(filters.get("to"), "to", errors)
        ids = {name: _parse_ids(filters.get(name), name, errors) for name in ID_FILTERS}
        # `federal_project` — имя query-параметра; `federal_project_id` — имя поля (синоним)
        projects = _parse_ids(
            filters.get("federal_project") or filters.get("federal_project_id"),
            "federal_project",
            errors,
        )
        if len(projects) > 1:
            errors.append(_invalid("federal_project", "too_many_values", "Один федеральный проект"))
        threshold: int | None = None
        raw_threshold = filters.get("threshold_days")
        if raw_threshold not in (None, ""):
            try:
                threshold = int(raw_threshold)
            except (TypeError, ValueError):
                threshold = None
            if threshold is None or not 1 <= threshold <= 60:
                errors.append(
                    _invalid("threshold_days", "invalid_value", "Порог — целое от 1 до 60 дней")
                )
                threshold = None
        region = filters.get("region") or None
        if errors:
            raise validation_error("Ошибка валидации данных", details=errors)
        return cls(
            workflow_type=workflow_type,
            date_from=date_from,
            date_to=date_to,
            region=str(region) if region is not None else None,
            federal_project_id=projects[0] if projects else None,
            threshold_days=threshold,
            **ids,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().astimezone(REPORT_TZ).date()


def _day_start(day: date) -> datetime:
    """00:00 МСК — граница периода (полуинтервал [from 00:00, to+1 00:00))."""
    return datetime.combine(day, time.min, REPORT_TZ)


@dataclass(frozen=True)
class _Window:
    start: datetime | None  # включительно
    end: datetime | None  # исключительно

    @classmethod
    def of(cls, date_from: date | None, date_to: date | None) -> _Window:
        return cls(
            start=_day_start(date_from) if date_from else None,
            end=_day_start(date_to + timedelta(days=1)) if date_to else None,
        )

    @property
    def is_open(self) -> bool:
        return self.start is None and self.end is None


def _ts(value: datetime) -> sa.BindParameter[Any]:
    return sa.literal(value, type_=sa.TIMESTAMP(timezone=True))


def _in_window(column: Any, window: _Window) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if window.start is not None:
        conditions.append(column >= _ts(window.start))
    if window.end is not None:
        conditions.append(column < _ts(window.end))
    return conditions


def _week_bucket(column: Any) -> Any:
    # единица и зона — литералы, не bind-параметры: одно выражение в SELECT и GROUP BY
    # рендерится одинаково (иначе GroupingError, см. kam_activity._bucket_expr)
    return func.date_trunc(
        sa.literal_column("'week'"),
        func.timezone(sa.literal_column("'Europe/Moscow'"), column),
    ).label("week")


def report_scope(viewer: AppUser) -> DealScope:
    """Область отчётов зрителя: `reports:read` в процессах ролей, «все КАМы» — при
    `reports:all` и записях `all` в процессе, иначе только свои заявки (§7.2)."""
    return viewer.access.deal_scope("reports:read", viewer.id, wide_permission="reports:all")


def _scope_or_all(scope: DealScope | None) -> DealScope:
    # None — прежний контракт «без ограничения» (служебные вызовы и старые тесты)
    return scope if scope is not None else DealScope.unrestricted()


def _envelope(code: str, params: ReportParams, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "report": code,
        "generated_at": _now().isoformat(),
        "params": params.as_json(),
        **body,
    }


# --------------------------------------------------------------------------------------
# Единый слой фильтров
# --------------------------------------------------------------------------------------


def _apply_dims(
    stmt: sa.Select,
    params: ReportParams,
    *,
    products: bool = True,
    programs: bool = True,
    directions: bool = True,
) -> sa.Select:
    """Измерения панели по заявке: процесс, КАМы, вузы, ИТ-направления, продукты,
    программы (`IN`; направление — `direction_match` по программе и продукту заявки).

    `products=False`/`programs=False`/`directions=False` — когда измерение применяется к
    самой сущности отчёта (реестр программ фильтруется по `program.product_id` и
    направлению программы, а не по заявке).
    """
    if params.workflow_type:
        stmt = stmt.where(Deal.pipeline == params.workflow_type)
    if params.kam_ids:
        stmt = stmt.where(Deal.responsible_user_id.in_(params.kam_ids))
    if params.university_ids:
        stmt = stmt.where(Deal.university_id.in_(params.university_ids))
    if products and params.product_ids:
        stmt = stmt.where(Deal.product_id.in_(params.product_ids))
    if programs and params.program_ids:
        stmt = stmt.where(Deal.program_id.in_(params.program_ids))
    if directions and params.direction_ids:
        stmt = stmt.where(direction_match(Deal.program_id, Deal.product_id, params.direction_ids))
    return stmt


def _deal_filters(stmt: sa.Select, params: ReportParams, scope: DealScope) -> sa.Select:
    """Когорта заявок, созданных в периоде (МСК), с измерениями панели и областью."""
    stmt = stmt.where(*_in_window(Deal.created_at, _Window.of(params.date_from, params.date_to)))
    stmt = _apply_dims(stmt, params)
    stmt = scope.apply(stmt)
    return stmt.where(Deal.status != "archived")


def _kam_universities(kam_ids: Iterable[uuid.UUID]) -> sa.Select:
    """Вузы, закреплённые за КАМами, — перевод фильтра «КАМ» на студентов и вузы."""
    return select(University.id).where(University.kam_user_id.in_(tuple(kam_ids)))


def _student_dims(stmt: sa.Select, params: ReportParams) -> sa.Select:
    """Измерения панели по студенту: вуз, КАМ (вузы КАМа), программа, продукт,
    ИТ-направление (программы или продукта студента), ФП."""
    if params.university_ids:
        stmt = stmt.where(Student.university_id.in_(params.university_ids))
    if params.kam_ids:
        stmt = stmt.where(Student.university_id.in_(_kam_universities(params.kam_ids)))
    if params.program_ids:
        stmt = stmt.where(Student.program_id.in_(params.program_ids))
    if params.product_ids:
        stmt = stmt.where(
            sa.or_(
                Student.product_id.in_(params.product_ids),
                Student.program_id.in_(
                    select(Program.id).where(Program.product_id.in_(params.product_ids))
                ),
            )
        )
    if params.direction_ids:
        stmt = stmt.where(
            direction_match(Student.program_id, Student.product_id, params.direction_ids)
        )
    if params.federal_project_id:
        stmt = stmt.where(Student.federal_project_id == params.federal_project_id)
    return stmt


def _stuck_conditions(default_days: int, threshold_days: int | None = None) -> list[Any]:
    """Открытая заявка на нетерминальном этапе дольше порога (каскад этап → процесс →
    глобальная настройка); требует join `WorkflowStage` и `Workflow`."""
    threshold = (
        sa.literal(threshold_days)
        if threshold_days is not None
        else func.coalesce(
            WorkflowStage.stuck_threshold_days,
            Workflow.stuck_threshold_days,
            default_days,
        )
    )
    return [
        Deal.status == "open",
        WorkflowStage.is_terminal.is_(False),
        func.now() - Deal.stage_entered_at > func.make_interval(0, 0, 0, threshold),
    ]


async def _workflows_with_stages(
    session: AsyncSession, workflow_types: Iterable[str]
) -> list[tuple[Workflow, list[WorkflowStage]]]:
    codes = [wf for wf in WORKFLOW_TYPES if wf in set(workflow_types)]
    if not codes:
        return []
    workflows = {
        workflow.code: workflow
        for workflow in (await session.execute(select(Workflow).where(Workflow.code.in_(codes))))
        .scalars()
        .all()
    }
    if not workflows:
        return []
    stages = (
        (
            await session.execute(
                select(WorkflowStage)
                .where(
                    WorkflowStage.workflow_id.in_([w.id for w in workflows.values()]),
                    WorkflowStage.deleted_at.is_(None),
                )
                .order_by(WorkflowStage.sort_order, WorkflowStage.name)
            )
        )
        .scalars()
        .all()
    )
    result = []
    for code in codes:
        workflow = workflows.get(code)
        if workflow is not None:
            result.append((workflow, [s for s in stages if s.workflow_id == workflow.id]))
    return result


def _panel_workflows(params: ReportParams, scope: DealScope) -> list[str]:
    """Процессы секции: выбранный — он; «Все» — все процессы области зрителя
    (никакой молчаливой подстановки B2B)."""
    if params.workflow_type:
        return [params.workflow_type]
    return [wf for wf in WORKFLOW_TYPES if wf in scope.workflow_types]


# --------------------------------------------------------------------------------------
# «Завершено» и «Отказ» (D16)
# --------------------------------------------------------------------------------------


def _closed_at_expr(status: str) -> ColumnElement[Any]:
    """Момент закрытия заявки с исходом `status`: ПОСЛЕДНИЙ вход в терминальный этап с этим
    исходом по истории переходов. Не `deal.updated_at`: он меняется любой правкой карточки
    (сумма, ответственный, комментарий) — закрытая полгода назад заявка «завершалась» бы
    заново. Строки перевода схемы (`system_migration`) — не закрытие; события LMS по уже
    закрытой заявке (`integration`, from = to) этап не меняют — тоже не закрытие (как
    `stage_entry_condition` у отчёта КАМов). Заявки без строки истории (ручная правка БД) —
    по `stage_entered_at` текущего этапа."""
    return sa.func.coalesce(
        select(func.max(DealStageHistory.created_at))
        .join(WorkflowStage, WorkflowStage.id == DealStageHistory.to_stage_id)
        .where(
            DealStageHistory.deal_id == Deal.id,
            DealStageHistory.kind != "system_migration",
            DealStageHistory.from_stage_id.is_distinct_from(DealStageHistory.to_stage_id),
            WorkflowStage.is_terminal.is_(True),
            WorkflowStage.terminal_outcome == status,
        )
        .correlate(Deal)
        .scalar_subquery(),
        Deal.stage_entered_at,
    )


def _closed_between_stmt(
    status: str,
    start: date | None,
    end: date | None,
    params: ReportParams,
    scope: DealScope,
) -> sa.Select:
    """«Завершено за период» (D16): заявки со статусом `won`/`lost`, чей последний вход в
    терминальный этап с этим исходом (`deal_stage_history`) попал в [start, end] по МСК.

    Та же семантика, что «Выиграно / Отказ» в отчёте «Активность КАМов», и та же, что у
    серий «Завершено»/«Отказ» динамики, — цифры KPI, динамики и отчёта КАМов сходятся.
    Измерения панели — те же, что у остальных секций (`_apply_dims`).
    """
    stmt = select(func.count()).select_from(Deal).where(Deal.status == status)
    stmt = _apply_dims(stmt, params)
    stmt = scope.apply(stmt)
    window = _Window.of(start, end)
    if not window.is_open:
        stmt = stmt.where(*_in_window(_closed_at_expr(status), window))
    return stmt


def _closed_weekly_stmt(
    status: str, window: _Window, params: ReportParams, scope: DealScope
) -> sa.Select:
    """Недельные корзины (МСК) закрытий с исходом `status` — для динамики."""
    inner = select(_closed_at_expr(status).label("closed_at")).where(Deal.status == status)
    inner = scope.apply(_apply_dims(inner, params)).subquery("closed")
    week = _week_bucket(inner.c.closed_at)
    return select(week, func.count()).where(*_in_window(inner.c.closed_at, window)).group_by(week)


# --------------------------------------------------------------------------------------
# Отчёты
# --------------------------------------------------------------------------------------


async def report_funnel(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """«Заявки по текущему этапу»: заявки, созданные в периоде (когорта), по этапу, на
    котором они сейчас; все измерения панели. Это срез, не конверсия «дошли до этапа» —
    её считает отчёт «Активность КАМов» по истории.

    Процесс не выбран («Все») — этапы всех процессов области подряд, у каждого элемента
    `workflow_type` (ничего не подменяется на B2B). Терминальные этапы помечены `outcome`
    (`won`/`lost`): «Отказ» фронт показывает отдельно, не хвостом последовательности.
    """
    scope = _scope_or_all(scope)
    workflows = await _workflows_with_stages(session, _panel_workflows(params, scope))
    if not workflows:
        return {"series": [], "workflow_types": []}
    stmt = _deal_filters(
        select(Deal.stage_id, func.count(), func.coalesce(func.sum(Deal.amount), 0)).where(
            Deal.workflow_id.in_([workflow.id for workflow, _stages in workflows])
        ),
        params,
        scope,
    ).group_by(Deal.stage_id)
    counts = {
        stage_id: (count, amount) for stage_id, count, amount in (await session.execute(stmt)).all()
    }
    several = len(workflows) > 1
    series = []
    for workflow, stages in workflows:
        group = WORKFLOW_LABELS.get(workflow.code, workflow.code.upper())
        for stage in stages:
            count, amount = counts.get(stage.id, (0, 0))
            series.append(
                {
                    "key": stage.code,
                    "status_id": str(stage.id),
                    # при нескольких процессах подпись однозначна и в плоской выгрузке
                    "label": f"{group} · {stage.name}" if several else stage.name,
                    "stage_label": stage.name,
                    "color": stage.color,
                    "value": int(count),
                    "amount": format(amount, "f"),
                    "workflow_type": workflow.code,
                    "sort_order": stage.sort_order,
                    "is_terminal": stage.is_terminal,
                    "outcome": stage.terminal_outcome if stage.is_terminal else None,
                }
            )
    return {"series": series, "workflow_types": [workflow.code for workflow, _s in workflows]}


async def report_kam_workload(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Нагрузка КАМов: открытые заявки НА ТЕКУЩУЮ ДАТУ (период не применяется — это срез
    «сейчас») по этапам, вузы в работе, зависшие; КАМы, вузы, продукты, программы и
    процесс — фильтры панели. `by_status` — в порядке этапов процесса (`sort_order`), у
    элемента и `{code,count}`, и `{key,value}` (формат series)."""
    scope = _scope_or_all(scope)
    # «КАМ» — роль с правом requests:kam (в выбранном процессе, если он задан)
    kam_roles = get_catalog_snapshot().role_codes_with("requests:kam", params.workflow_type)
    users_stmt = select(AppUser).where(
        AppUser.roles.overlap(list(kam_roles)), AppUser.is_active.is_(True)
    )
    if scope.own_user_id is not None:
        users_stmt = users_stmt.where(AppUser.id == scope.own_user_id)
    if params.kam_ids:
        users_stmt = users_stmt.where(AppUser.id.in_(params.kam_ids))
    kams = list((await session.execute(users_stmt)).scalars().all())

    base = (
        select(
            Deal.responsible_user_id,
            Deal.stage_id,
            Deal.pipeline,
            WorkflowStage.code,
            WorkflowStage.name,
            WorkflowStage.color,
            WorkflowStage.sort_order,
            func.count(),
        )
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .where(Deal.status == "open")
        .group_by(
            Deal.responsible_user_id,
            Deal.stage_id,
            Deal.pipeline,
            WorkflowStage.code,
            WorkflowStage.name,
            WorkflowStage.color,
            WorkflowStage.sort_order,
        )
    )
    base = scope.apply(_apply_dims(base, params))
    by_stage: dict[uuid.UUID, list[dict[str, Any]]] = {}
    totals: dict[uuid.UUID, int] = {}
    for user_id, stage_id, pipeline, code, name, color, sort_order, count in (
        await session.execute(base)
    ).all():
        by_stage.setdefault(user_id, []).append(
            {
                "status_id": str(stage_id),
                "code": code,
                "key": code,
                "label": name,
                "color": color,
                "count": int(count),
                "value": int(count),
                "sort_order": int(sort_order),
                "workflow_type": pipeline,
            }
        )
        totals[user_id] = totals.get(user_id, 0) + int(count)

    default_days = await _global_stuck_default(session)
    stuck_stmt = (
        select(Deal.responsible_user_id, func.count())
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(*_stuck_conditions(default_days))
        .group_by(Deal.responsible_user_id)
    )
    stuck_stmt = scope.apply(_apply_dims(stuck_stmt, params))
    stuck = dict((await session.execute(stuck_stmt)).all())

    uni_stmt = (
        select(Deal.responsible_user_id, University.id, University.name, func.count())
        .join(University, University.id == Deal.university_id)
        .where(Deal.status == "open")
        .group_by(Deal.responsible_user_id, University.id, University.name)
    )
    uni_stmt = scope.apply(_apply_dims(uni_stmt, params))
    universities: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for user_id, university_id, university_name, count in (await session.execute(uni_stmt)).all():
        universities.setdefault(user_id, []).append(
            {"id": str(university_id), "name": university_name, "count": int(count)}
        )

    workflow_order = {wf: index for index, wf in enumerate(WORKFLOW_TYPES)}
    rows = []
    for kam in kams:
        kam_universities = sorted(
            universities.get(kam.id, []), key=lambda item: (-item["count"], item["name"])
        )
        rows.append(
            {
                "kam_id": str(kam.id),
                "kam": kam.full_name or kam.username,
                "kam_name": kam.full_name or kam.username,
                "active_requests": totals.get(kam.id, 0),
                "universities": len(kam_universities),
                "stuck": int(stuck.get(kam.id, 0)),
                "by_status": sorted(
                    by_stage.get(kam.id, []),
                    key=lambda item: (
                        workflow_order.get(item["workflow_type"], 99),
                        item["sort_order"],
                        item["label"],
                    ),
                ),
                "top_universities": kam_universities[:TOP_UNIVERSITIES_PER_KAM],
            }
        )
    rows.sort(key=lambda item: (-item["active_requests"], item["kam_name"]))
    return {
        "rows": rows,
        "columns": [
            {"key": "kam", "label": "КАМ"},
            {"key": "active_requests", "label": "Активные заявки"},
            {"key": "universities", "label": "Вузы в работе"},
            {"key": "stuck", "label": "Зависшие"},
        ],
    }


async def report_stuck(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Зависшие заявки на текущую дату (порог каскадом или `threshold_days`), измерения
    панели — `IN`."""
    scope = _scope_or_all(scope)
    default_days = await _global_stuck_default(session)
    threshold_expr = (
        sa.literal(params.threshold_days)
        if params.threshold_days is not None
        else func.coalesce(
            WorkflowStage.stuck_threshold_days,
            Workflow.stuck_threshold_days,
            default_days,
        )
    )
    stmt = (
        select(Deal, WorkflowStage, Workflow, AppUser, threshold_expr.label("threshold"))
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .join(AppUser, AppUser.id == Deal.responsible_user_id)
        .where(*_stuck_conditions(default_days, params.threshold_days))
        .order_by(Deal.stage_entered_at)
        .limit(STUCK_ROWS_LIMIT)
    )
    stmt = scope.apply(_apply_dims(stmt, params))
    now = _now()
    rows = []
    for deal, stage, _workflow, assignee, threshold in (await session.execute(stmt)).all():
        days = max((now - deal.stage_entered_at).days, 0)
        rows.append(
            {
                "request_id": str(deal.id),
                "title": deal.title,
                "workflow_type": deal.pipeline,
                "status": {"id": str(stage.id), "code": stage.code, "name": stage.name},
                "assignee": {
                    "id": str(assignee.id),
                    "full_name": assignee.full_name or assignee.username,
                },
                "stuck_days": days,
                "threshold_days": int(threshold),
                "status_updated_at": deal.stage_entered_at.isoformat(),
            }
        )
    return {
        "rows": rows,
        "columns": [
            {"key": "title", "label": "Заявка"},
            {"key": "status", "label": "Этап"},
            {"key": "assignee", "label": "Ответственный"},
            {"key": "stuck_days", "label": "Дней на этапе"},
            {"key": "threshold_days", "label": "Порог, дней"},
        ],
    }


async def report_universities_coverage(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Вузы по регионам; фильтры: регион, вузы (`IN`), КАМы — вузы, закреплённые за ними.
    Продукты, программы и ИТ-направления к охвату вузов не применяются (это реестр вузов,
    а не заявок)."""
    scope = _scope_or_all(scope)
    active_contract = (
        select(Contract.id)
        .where(Contract.university_id == University.id, Contract.status == "active")
        .exists()
    )
    open_deal = (
        select(Deal.id)
        .where(
            Deal.university_id == University.id,
            Deal.status == "open",
            scope.workflow_clause(),
        )
        .exists()
    )
    has_contact = (
        select(UniversityContact.id)
        .where(UniversityContact.university_id == University.id)
        .exists()
    )
    # одно labeled-выражение и в SELECT, и в GROUP BY: два независимых
    # func.coalesce(...) под asyncpg рендерятся разными bind-параметрами
    # ($1 и $4), и Postgres отвечает GroupingError «must appear in GROUP BY»
    region_expr = func.coalesce(University.region, "Без региона").label("region")
    stmt = (
        select(
            region_expr,
            func.count(),
            func.count().filter(active_contract),
            func.count().filter(open_deal),
            func.count().filter(~has_contact),
        )
        .where(University.is_active.is_(True))
        .group_by(region_expr)
        .order_by(func.count().desc())
    )
    if params.region:
        stmt = stmt.where(University.region == params.region)
    if params.university_ids:
        stmt = stmt.where(University.id.in_(params.university_ids))
    if params.kam_ids:
        stmt = stmt.where(University.kam_user_id.in_(params.kam_ids))
    rows = [
        {
            "region": region,
            "total": int(total),
            "with_active_contract": int(with_contract),
            "in_progress": int(in_progress),
            "without_contact": int(without_contact),
        }
        for region, total, with_contract, in_progress, without_contact in (
            await session.execute(stmt)
        ).all()
    ]
    return {
        "rows": rows,
        "columns": [
            {"key": "region", "label": "Регион"},
            {"key": "total", "label": "Вузов"},
            {"key": "with_active_contract", "label": "С активным договором"},
            {"key": "in_progress", "label": "В работе"},
            {"key": "without_contact", "label": "Без контакта"},
        ],
    }


def _learning_stage_ids() -> sa.Select:
    """Этапы обучения всех процессов: вход передаёт заявку в LMS или код `in_learning`
    (удалённые этапы тоже — на них ссылается история)."""
    return select(WorkflowStage.id).where(
        sa.or_(
            WorkflowStage.triggers_lms_handover.is_(True),
            WorkflowStage.code == LEARNING_STAGE_CODE,
        )
    )


def _in_learning_clause(window: _Window) -> ColumnElement[bool]:
    """Заявка на этапе обучения: без периода — сейчас; с периодом — хотя бы в один момент
    периода (вход до конца периода и нет выхода до его начала, по истории переходов).
    Заявка без строк истории — по текущему этапу и `stage_entered_at`."""
    learning = _learning_stage_ids()
    current: list[ColumnElement[bool]] = [
        Deal.status == "open",
        Deal.stage_id.in_(learning),
    ]
    if window.is_open:
        return sa.and_(*current)
    if window.end is not None:
        current.append(Deal.stage_entered_at < _ts(window.end))
    entered = aliased(DealStageHistory, name="learn_in")
    left = aliased(DealStageHistory, name="learn_out")
    stint: list[ColumnElement[bool]] = [
        entered.deal_id == Deal.id,
        entered.to_stage_id.in_(learning),
    ]
    if window.end is not None:
        stint.append(entered.created_at < _ts(window.end))
    if window.start is not None:
        left_before_start = (
            select(left.id)
            .where(
                left.deal_id == entered.deal_id,
                left.created_at > entered.created_at,
                left.created_at < _ts(window.start),
                left.to_stage_id.not_in(learning),
            )
            .exists()
        )
        stint.append(~left_before_start)
    return sa.or_(select(entered.id).where(*stint).exists(), sa.and_(*current))


def _studying_clause(window: _Window) -> ColumnElement[bool]:
    """Студент обучается: без периода — статус сейчас; с периодом — был в статусе
    «Обучается» хотя бы в один момент периода (по истории статусов; без истории — по
    текущему статусу и `status_changed_at`)."""
    current: list[ColumnElement[bool]] = [Student.funnel_status == STUDYING_STATUS]
    if window.is_open:
        return current[0]
    if window.end is not None:
        current.append(Student.status_changed_at < _ts(window.end))
    entered = aliased(StudentStatusHistory, name="study_in")
    left = aliased(StudentStatusHistory, name="study_out")
    stint: list[ColumnElement[bool]] = [
        entered.student_id == Student.id,
        entered.to_status == STUDYING_STATUS,
    ]
    if window.end is not None:
        stint.append(entered.created_at < _ts(window.end))
    if window.start is not None:
        left_before_start = (
            select(left.id)
            .where(
                left.student_id == entered.student_id,
                left.created_at > entered.created_at,
                left.created_at < _ts(window.start),
                left.to_status != STUDYING_STATUS,
            )
            .exists()
        )
        stint.append(~left_before_start)
    return sa.or_(select(entered.id).where(*stint).exists(), sa.and_(*current))


def programs_demand_stmt(params: ReportParams, scope: DealScope) -> sa.Select:
    """Три показателя востребованности (§2 ТЗ) по каждой активной программе:

    - `requests_count` — заявки на обучение по программе (созданные в периоде, без архива),
      `open_requests` — из них открытые;
    - `students_studying` — обучающиеся: студенты программы в статусе «Обучается»
      (в периоде — хотя бы день периода);
    - `parallel_streams` — параллельные потоки: заявки программы на этапе обучения
      (в периоде — хотя бы день периода), без схлопывания по вузам: два потока в одном
      вузе — два потока.

    Заявки — в процессах области зрителя без ограничения «только свои» (спрос на
    программы — общий), с фильтрами процесса, КАМов и вузов. Студенты — по вузам (и вузам
    КАМов). Продукты, программы и ИТ-направления фильтруют сам реестр программ
    (направление программы, без него — её продукта).
    """
    window = _Window.of(params.date_from, params.date_to)
    workflow_clause = scope.workflow_clause()

    requests = (
        select(
            Deal.program_id.label("program_id"),
            func.count().label("requests"),
            func.count().filter(Deal.status == "open").label("open_requests"),
        )
        .where(
            Deal.program_id.is_not(None),
            Deal.status != "archived",
            workflow_clause,
            *_in_window(Deal.created_at, window),
        )
        .group_by(Deal.program_id)
    )
    requests = _apply_dims(
        requests, params, products=False, programs=False, directions=False
    ).subquery("req")

    streams = (
        select(Deal.program_id.label("program_id"), func.count().label("streams"))
        .where(
            Deal.program_id.is_not(None),
            Deal.status != "archived",
            workflow_clause,
            _in_learning_clause(window),
        )
        .group_by(Deal.program_id)
    )
    streams = _apply_dims(
        streams, params, products=False, programs=False, directions=False
    ).subquery("streams")

    students = (
        select(Student.program_id.label("program_id"), func.count().label("students"))
        .where(Student.program_id.is_not(None), _studying_clause(window))
        .group_by(Student.program_id)
    )
    if params.university_ids:
        students = students.where(Student.university_id.in_(params.university_ids))
    if params.kam_ids:
        students = students.where(Student.university_id.in_(_kam_universities(params.kam_ids)))
    students = students.subquery("students")

    stmt = (
        select(
            Program.id,
            Program.name,
            Program.product_id,
            Program.priority_rank,
            func.coalesce(requests.c.requests, 0),
            func.coalesce(requests.c.open_requests, 0),
            func.coalesce(students.c.students, 0),
            func.coalesce(streams.c.streams, 0),
        )
        .outerjoin(requests, requests.c.program_id == Program.id)
        .outerjoin(students, students.c.program_id == Program.id)
        .outerjoin(streams, streams.c.program_id == Program.id)
        .where(Program.is_active.is_(True))
    )
    if params.product_ids:
        stmt = stmt.where(Program.product_id.in_(params.product_ids))
    if params.program_ids:
        stmt = stmt.where(Program.id.in_(params.program_ids))
    if params.direction_ids:
        stmt = stmt.where(direction_match(Program.id, Program.product_id, params.direction_ids))
    return stmt


def demand_index(rows: list[dict[str, Any]]) -> None:
    """Расчётный спрос (фичефлаг `auto_ranking`): индекс 0–100 — среднее трёх долей от
    максимума по показанным программам. Пишет `computed_demand` в строки на месте.
    Справочный: ручной приоритет (`priority_rank`) не меняет — решение kickoff N7."""
    keys = ("requests_count", "students_studying", "parallel_streams")
    peaks = {key: max((row[key] for row in rows), default=0) for key in keys}
    for row in rows:
        shares = [row[key] / peaks[key] if peaks[key] else 0.0 for key in keys]
        row["computed_demand"] = round(100 * sum(shares) / len(keys), 1)


def sort_programs_demand(rows: list[dict[str, Any]], *, by_index: bool) -> list[dict[str, Any]]:
    """Сортировка по востребованности — ДО любого ограничения длины: при авторанжировании
    по индексу, иначе заявки → обучающиеся → потоки; при равенстве — ручной приоритет."""

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        head = (-(row["computed_demand"] or 0),) if by_index else ()
        return (
            *head,
            -row["requests_count"],
            -row["students_studying"],
            -row["parallel_streams"],
            row["priority_rank"],
            row["name"],
        )

    return sorted(rows, key=key)


async def _auto_ranking_enabled(session: AsyncSession) -> bool:
    from app.modules.admin.service import get_flags

    return bool((await get_flags(session)).get("auto_ranking"))


async def report_programs_demand(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Востребованность программ: три показателя ТЗ (см. `programs_demand_stmt`),
    ручной приоритет рядом; при фичефлаге `auto_ranking` — расчётный индекс и сортировка
    по нему. Без обрезки до сортировки: строки упорядочены по востребованности, а не по
    ручному топ-20."""
    scope = _scope_or_all(scope)
    auto_ranking = await _auto_ranking_enabled(session)
    rows = [
        {
            "program_id": str(program_id),
            "name": name,
            "product_id": str(product_id) if product_id else None,
            "priority_rank": priority_rank,
            "requests_count": int(total),
            "open_requests": int(open_),
            "students_studying": int(studying),
            "parallel_streams": int(streams),
            "computed_demand": None,
        }
        for program_id, name, product_id, priority_rank, total, open_, studying, streams in (
            await session.execute(programs_demand_stmt(params, scope))
        ).all()
    ]
    if auto_ranking:
        demand_index(rows)
    rows = sort_programs_demand(rows, by_index=auto_ranking)[:PROGRAMS_DEMAND_CAP]
    columns = [
        {"key": "name", "label": "Программа"},
        {"key": "requests_count", "label": "Заявки на обучение"},
        {"key": "open_requests", "label": "Из них открыто"},
        {"key": "students_studying", "label": "Обучающиеся"},
        {"key": "parallel_streams", "label": "Параллельные потоки"},
        {"key": "priority_rank", "label": "Ручной приоритет (меньше = выше)"},
    ]
    if auto_ranking:
        columns.append({"key": "computed_demand", "label": "Расчётный спрос (0–100)"})
    return {
        "rows": rows,
        "columns": columns,
        "auto_ranking": auto_ranking,
        "formula": DEMAND_FORMULA if auto_ranking else None,
        "period": (
            {
                "from": params.date_from.isoformat() if params.date_from else None,
                "to": params.date_to.isoformat() if params.date_to else None,
            }
            if params.has_period
            else None
        ),
    }


async def report_talent_pool_funnel(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Студенты по статусам на текущую дату; фильтры: вузы, КАМы (вузы, закреплённые за
    КАМом), программы, продукты, ИТ-направления, федеральный проект. Период не применяется
    (срез)."""
    stmt = _student_dims(
        select(Student.funnel_status, func.count()).group_by(Student.funnel_status), params
    )
    counts = dict((await session.execute(stmt)).all())
    series = [
        {
            "key": status,
            "label": FUNNEL_LABELS[status],
            "color": FUNNEL_COLORS[status],
            "value": int(counts.get(status, 0)),
        }
        for status in ("candidate", "studying", "graduate", "talent_pool")
    ]
    return {"series": series}


def dynamics_period(params: ReportParams, today: date) -> tuple[date, date]:
    date_to = params.date_to or today
    date_from = params.date_from or (date_to - timedelta(weeks=DEFAULT_DYNAMICS_WEEKS))
    return date_from, date_to


async def report_dynamics(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Недели (МСК): «Создано», «Завершено» (`won`) и «Отказ» (`lost`). Закрытия — по тому
    же моменту, что KPI «Завершено» (`_closed_at_expr`): за один период сумма точек
    «Завершено» равна KPI. Все измерения панели. Без периода — последние 12 недель."""
    scope = _scope_or_all(scope)
    date_from, date_to = dynamics_period(params, _today())
    window = _Window.of(date_from, date_to)

    created_week = _week_bucket(Deal.created_at)
    created_stmt = _deal_filters(
        select(created_week, func.count()),
        _replace_period(params, date_from, date_to),
        scope,
    ).group_by(created_week)
    created = {week.date(): int(count) for week, count in (await session.execute(created_stmt))}

    async def closed(status: str) -> dict[date, int]:
        stmt = _closed_weekly_stmt(status, window, params, scope)
        return {week.date(): int(count) for week, count in (await session.execute(stmt))}

    won = await closed("won")
    lost = await closed("lost")

    # непрерывная сетка недель от начала периода
    weeks: list[date] = []
    cursor = date_from - timedelta(days=date_from.weekday())
    while cursor <= date_to:
        weeks.append(cursor)
        cursor += timedelta(weeks=1)

    def points(values: dict[date, int]) -> list[dict[str, Any]]:
        return [{"date": week.isoformat(), "value": values.get(week, 0)} for week in weeks]

    return {
        "series": [
            {"key": "created", "label": "Создано", "points": points(created)},
            # ключ `closed` сохранён для выгрузок; состав — как у KPI «Завершено» (won)
            {"key": "closed", "label": "Завершено", "points": points(won)},
            {"key": "lost", "label": "Отказ", "points": points(lost)},
        ],
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
    }


def _replace_period(params: ReportParams, date_from: date, date_to: date) -> ReportParams:
    return replace(params, date_from=date_from, date_to=date_to)


def kpi_period(params: ReportParams, today: date) -> tuple[date, date, bool]:
    """Период KPI: выбранный; без выбора — последние 30 дней по МСК включительно."""
    is_default = not params.has_period
    date_to = params.date_to or today
    date_from = params.date_from or (date_to - timedelta(days=DEFAULT_KPI_DAYS - 1))
    return date_from, date_to, is_default


def previous_period(date_from: date, date_to: date) -> tuple[date, date]:
    """Интервал той же длины непосредственно перед текущим (как в «Активности КАМов»)."""
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    return prev_to - timedelta(days=days - 1), prev_to


async def _kpi(session: AsyncSession, params: ReportParams, scope: DealScope) -> dict[str, Any]:
    """KPI-строка дашборда с одинаковыми измерениями панели:
    «Заявок активных» и «Зависших» — на текущую дату; «Завершено» (won), «Отказ» (lost) и
    конверсия won / (won + lost) — за период `period` с дельтой к прошлому периоду."""
    scope = _scope_or_all(scope)
    active_stmt = select(func.count()).select_from(Deal).where(Deal.status == "open")
    active_stmt = scope.apply(_apply_dims(active_stmt, params))
    active = (await session.execute(active_stmt)).scalar_one()

    default_days = await _global_stuck_default(session)
    stuck_stmt = (
        select(func.count())
        .select_from(Deal)
        .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
        .join(Workflow, Workflow.id == Deal.workflow_id)
        .where(*_stuck_conditions(default_days))
    )
    stuck_stmt = scope.apply(_apply_dims(stuck_stmt, params))
    stuck_total = int((await session.execute(stuck_stmt)).scalar_one())

    async def _closed_between(status: str, start: date, end: date) -> int:
        stmt = _closed_between_stmt(status, start, end, params, scope)
        return int((await session.execute(stmt)).scalar_one())

    date_from, date_to, is_default = kpi_period(params, _today())
    prev_from, prev_to = previous_period(date_from, date_to)

    won = await _closed_between("won", date_from, date_to)
    lost = await _closed_between("lost", date_from, date_to)
    prev_won = await _closed_between("won", prev_from, prev_to)
    prev_lost = await _closed_between("lost", prev_from, prev_to)
    closed_total = won + lost
    conversion = round(won * 100 / closed_total, 1) if closed_total else None
    prev_closed_total = prev_won + prev_lost
    prev_conversion = round(prev_won * 100 / prev_closed_total, 1) if prev_closed_total else None

    # форма фронтенда (DashboardKpi): каждая метрика — {value, delta};
    # delta = null, когда сравнивать не с чем
    return {
        "active": {"value": int(active), "delta": None},
        "completed": {"value": won, "delta": won - prev_won},
        "lost": {"value": lost, "delta": lost - prev_lost},
        "conversion": {
            "value": conversion if conversion is not None else 0.0,
            "delta": round(conversion - prev_conversion, 1)
            if conversion is not None and prev_conversion is not None
            else None,
        },
        "stuck": {"value": stuck_total, "delta": None},
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "days": (date_to - date_from).days + 1,
            "is_default": is_default,
            "previous": {"from": prev_from.isoformat(), "to": prev_to.isoformat()},
        },
    }


async def report_dashboard(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Агрегат всех виджетов одним запросом (ux.md §6.3) — один набор фильтров на все
    секции; семантика периода каждой секции — в её docstring."""
    scope = _scope_or_all(scope)
    funnel = await report_funnel(session, params, scope)
    dynamics = await report_dynamics(session, params, scope)
    programs = await report_programs_demand(session, params, scope)
    # секции — в форме frontend DashboardReport: funnel/dynamics/talent_pool_funnel
    # оборачиваются в {series}, kam_workload/programs_demand — в {rows}
    return {
        "kpi": await _kpi(session, params, scope),
        "funnel": funnel,
        "dynamics": dynamics,
        "kam_workload": {"rows": (await report_kam_workload(session, params, scope))["rows"]},
        "programs_demand": {
            "rows": programs["rows"],
            "auto_ranking": programs["auto_ranking"],
            "formula": programs["formula"],
            "period": programs["period"],
        },
        "talent_pool_funnel": {
            "series": (await report_talent_pool_funnel(session, params, scope))["series"]
        },
    }


async def report_signals(
    session: AsyncSession, params: ReportParams, scope: DealScope
) -> dict[str, Any]:
    """Карточка «Сигналы» (зависшие, без ответственного КАМа, лицензии, вузы без активности)
    на текущий момент — `signals.build_signals`. Модуль сигналов сам импортирует этот
    (условия «зависла», измерения панели), поэтому импорт — при вызове. В выгрузки
    (`REPORT_CODES`) не входит: это экранная сводка со ссылками на списки."""
    from app.modules.reporting.signals import build_signals

    return await build_signals(session, params, _scope_or_all(scope))


_BUILDERS = {
    "funnel": report_funnel,
    "kam-workload": report_kam_workload,
    "stuck": report_stuck,
    "universities-coverage": report_universities_coverage,
    "programs-demand": report_programs_demand,
    "talent-pool-funnel": report_talent_pool_funnel,
    "dynamics": report_dynamics,
    "dashboard": report_dashboard,
    "signals": report_signals,
}

# отчёты, чей ответ зависит от фичефлага auto_ranking (флаг — в ключе кэша)
_RANKING_CODES = frozenset({"programs-demand", "dashboard"})


def _cache_suffix(
    code: str, params: ReportParams, scope: DealScope, extra: Mapping[str, Any] | None = None
) -> str:
    digest = hashlib.sha1(
        orjson.dumps(
            {"params": params.as_json(), "scope": scope.cache_key(), "extra": dict(extra or {})},
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()
    return f"dash:{code}:{digest}"


async def build_report(
    session: AsyncSession, code: str, viewer: AppUser, params: ReportParams
) -> dict[str, Any]:
    """Отчёт с кэшем cache-aside (TTL 60 с); недоступный KeyDB — чтение из PG."""
    builder = _BUILDERS[code]
    scope = report_scope(viewer)
    extra = (
        {"auto_ranking": await _auto_ranking_enabled(session)} if code in _RANKING_CODES else None
    )
    cache = get_cache()
    suffix = _cache_suffix(code, params, scope, extra)
    cached = await cache.get_json(suffix)
    if cached is not None:
        return cached
    body = await builder(session, params, scope)
    result = _envelope(code, params, body)
    await cache.set_json(suffix, result, REPORT_CACHE_TTL_SECONDS)
    return result
