"""Pydantic-схемы ответов отчётов «Активность КАМов» (api-contract.md §7.2.1) и
«Взаимодействия за период» (таблица по колонкам ТЗ ФТ4).

Нужны для качественного OpenAPI (Swagger показывают экспертам): каждое поле описано,
форма совпадает с тем, что собирает `kam_activity.py`. Деньги — строкой decimal
(api-contract.md §1.3), моменты времени — ISO 8601 с зоной, даты — ГГГГ-ММ-ДД (МСК).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


WorkflowType = Literal["b2b", "b2c"]
Outcome = Literal["won", "lost"]


# --- общие части ------------------------------------------------------------------------


class KamActivityParamsOut(_ApiModel):
    from_: date | None = Field(default=None, alias="from", serialization_alias="from")
    to: date | None = None
    workflow_type: WorkflowType | None = None
    kam_id: list[str] = Field(default_factory=list)
    university_id: list[str] = Field(default_factory=list)
    direction_id: list[str] = Field(
        default_factory=list, description="ИТ-направления фильтра (ключ есть, только если задан)"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ReportDirectionRef(_ApiModel):
    """Выбранное в фильтре ИТ-направление — подпись чипа и шапки выгрузки."""

    id: str
    code: str
    name: str


class KamActivityPeriod(_ApiModel):
    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    as_of: datetime = Field(description="«Данные на»: min(конец периода, сейчас)")
    days: int
    granularity: Literal["day", "week", "month"] | None = Field(
        default=None, description="Шаг динамики (только в сводке)"
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class KamActivityScope(_ApiModel):
    own_only: bool = Field(description="Отчёт только по своим заявкам (КАМ без «широких» прав)")
    own_user_id: str | None = None
    workflow_types: list[WorkflowType]


class KamActivityInteractions(_ApiModel):
    """Собственные действия ответственного КАМа в периоде; total = П + К + Ф + М."""

    transitions: int = Field(description="Ручные переходы (kind=manual)")
    returns: int = Field(description="Из них возвраты на предыдущий этап")
    comments: int
    files: int
    activities: int = Field(description="Мероприятия с вузом (слоты в периоде)")
    total: int


class StageChip(_ApiModel):
    id: str
    code: str
    name: str = Field(description="Удалённый этап — историческое название с «(удалён)»")
    color: str | None = None
    is_terminal: bool
    outcome: Outcome | None = None
    workflow_type: WorkflowType
    count: int


class RequestStage(_ApiModel):
    id: str
    code: str
    name: str
    color: str | None = None
    is_terminal: bool
    outcome: Outcome | None = None
    deleted: bool = False
    sort_order: int = 0
    workflow_type: WorkflowType


# --- детализация (А) ---------------------------------------------------------------------


class KamActivityRequestRow(_ApiModel):
    request_id: str
    title: str
    workflow_type: WorkflowType
    client: str | None = Field(default=None, description="Псевдоним B2C-клиента «Фамилия И.О.»")
    amount: str | None = None
    status: Literal["open", "won", "lost", "archived"]
    created_at: datetime
    created_in_period: bool
    closed_in_period: Outcome | None = None
    stage: RequestStage = Field(description="Этап на конец периода (восстановлен по истории)")
    stage_since: datetime
    days_on_stage: int
    threshold_days: int
    is_stuck: bool
    interactions: KamActivityInteractions
    last_contact_at: datetime | None = None


class KamActivityUniversityRow(_ApiModel):
    university_id: str | None = Field(description="null — узел «B2C-клиенты (без вуза)»")
    name: str
    short_name: str | None = None
    region: str | None = None
    assigned: bool = Field(description="Вуз закреплён за этим КАМом")
    requests_count: int
    new_requests: int
    won: int
    lost: int
    stuck: int
    interactions: KamActivityInteractions
    last_contact_at: datetime | None = None
    stages: list[StageChip]
    requests_truncated: bool = Field(
        description="Заявки опущены предохранителем размера — догрузить по kam_id + university_id"
    )
    requests: list[KamActivityRequestRow]


class KamActivityKamRow(_ApiModel):
    kam_id: str
    kam: str
    is_active: bool
    roles: list[str]
    universities_count: int
    universities_touched: int
    universities_assigned: int
    assigned_touched: int
    assigned_untouched: int
    coverage_pct: float | None = None
    requests_count: int
    new_requests: int
    new_amount: str | None = None
    won: int
    lost: int
    conversion: float | None = Field(default=None, description="Выиграно / закрыто, %")
    stuck: int
    interactions: KamActivityInteractions
    last_contact_at: datetime | None = None
    stages: list[StageChip]
    universities: list[KamActivityUniversityRow]


class KamActivityTotals(_ApiModel):
    kams: int
    universities_count: int
    universities_touched: int
    universities_assigned: int
    assigned_touched: int
    coverage_pct: float | None = None
    requests_count: int
    new_requests: int
    new_amount: str | None = None
    won: int
    lost: int
    conversion: float | None = None
    stuck: int
    interactions: KamActivityInteractions


class KamActivityReportOut(_ApiModel):
    report: Literal["kam-activity"]
    generated_at: datetime
    params: KamActivityParamsOut
    period: KamActivityPeriod
    scope: KamActivityScope
    totals: KamActivityTotals
    rows: list[KamActivityKamRow]


# --- сводка (Б) --------------------------------------------------------------------------


class KpiValue(_ApiModel):
    """Значение, значение прошлого периода той же длины и разница (проценты — в п.п.)."""

    value: float | int | str | None = None
    prev: float | int | str | None = None
    delta: float | int | str | None = None


class KamActivityKpi(_ApiModel):
    coverage_pct: KpiValue
    universities_touched: KpiValue
    universities_assigned: KpiValue
    assigned_touched: KpiValue
    requests_count: KpiValue
    new_requests: KpiValue
    new_amount: KpiValue
    won: KpiValue
    lost: KpiValue
    conversion: KpiValue
    interactions: KpiValue
    stuck: KpiValue
    talent_pool_added: KpiValue
    universities_with_contract: KpiValue = Field(
        default_factory=KpiValue,
        description="Вузы с действующим договором на конец периода (вид Б)",
    )
    students_studying: KpiValue = Field(
        default_factory=KpiValue,
        description="Студенты вузов отчёта в статусе «Обучается» на конец периода (вид Б)",
    )


class PreviousPeriod(_ApiModel):
    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    as_of: datetime
    days: int

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class StageConversionRow(_ApiModel):
    id: str
    code: str
    name: str
    color: str | None = None
    is_terminal: bool
    outcome: Outcome | None = None
    deleted: bool = False
    reached: int = Field(description="Заявок дошло до этапа (по истории, до as_of)")
    share_of_first_pct: float | None = None
    step_conversion_pct: float | None = None
    on_stage_at_end: int
    stuck: int
    avg_days: float | None = Field(default=None, description="Среднее пребывание, дней")
    intervals: int
    threshold_days: int | None = Field(
        default=None, description="Порог «зависания» этапа (этап → воронка → общий), дней"
    )


class DynamicsPoint(_ApiModel):
    date: date
    value: int


class DynamicsSeries(_ApiModel):
    key: Literal["transitions", "comments", "files", "activities", "new_requests"]
    label: str
    points: list[DynamicsPoint]


class KamActivityDynamics(_ApiModel):
    granularity: Literal["day", "week", "month"]
    series: list[DynamicsSeries]


class KamBreakdownRow(_ApiModel):
    kam_id: str
    kam: str
    is_active: bool
    universities_touched: int
    universities_assigned: int
    assigned_touched: int
    coverage_pct: float | None = None
    requests_count: int
    new_requests: int
    won: int
    lost: int
    conversion: float | None = None
    interactions: int
    interactions_prev: int
    interactions_detail: KamActivityInteractions
    stuck: int
    last_contact_at: datetime | None = None


class StuckStage(_ApiModel):
    id: str
    code: str
    name: str
    color: str | None = None
    is_terminal: bool
    outcome: Outcome | None = None


class StuckRequestRow(_ApiModel):
    request_id: str
    title: str
    workflow_type: WorkflowType
    university: str | None = Field(default=None, description="Вуз или псевдоним B2C-клиента")
    kam_id: str
    kam: str
    stage: StuckStage
    days_on_stage: int
    threshold_days: int
    overdue_days: int


class TalentStatusRow(_ApiModel):
    key: Literal["candidate", "studying", "graduate", "talent_pool"]
    label: str
    color: str
    value: int = Field(description="Студентов в статусе на конец периода")
    entered: int = Field(description="Перешли в статус за период")


class TalentProgramRow(_ApiModel):
    program_id: str
    name: str | None = None
    university: str | None = None
    candidate: int
    studying: int
    graduate: int
    talent_pool: int
    total: int


class KamActivityTalent(_ApiModel):
    """Студенты на программах из пула талантов — только агрегаты, без ПДн."""

    students_total: int
    on_programs: int = Field(description="Обучаются, выпустились или в пуле талантов")
    by_status: list[TalentStatusRow]
    talent_pool_added: KpiValue
    students_studying: KpiValue = Field(default_factory=KpiValue)
    programs: list[TalentProgramRow]


class UniversitiesStageRow(_ApiModel):
    id: str
    code: str
    name: str
    color: str | None = None
    is_terminal: bool
    outcome: Outcome | None = None
    value: int = Field(description="Вузов на этапе на конец периода")
    prev: int = Field(description="То же на конец прошлого периода той же длины")
    universities: list[str] = Field(description="Вузы этапа (до 8, по алфавиту)")


class UniversitiesWithoutRequests(_ApiModel):
    label: str
    value: int
    prev: int
    universities: list[str]


class UniversitiesByStage(_ApiModel):
    """Главная диаграмма вида Б: вузы B2B по самому продвинутому этапу заявок."""

    workflow_type: Literal["b2b"]
    rule: str = Field(description="Правило отнесения вуза к этапу (то же в UI и PDF)")
    total: int
    total_prev: int
    rows: list[UniversitiesStageRow]
    without_requests: UniversitiesWithoutRequests


class SummaryRisk(_ApiModel):
    key: Literal["stuck", "untouched", "lost"]
    label: str
    value: int
    note: str | None = None
    items: list[str] = Field(default_factory=list)


class KamActivitySummaryOut(_ApiModel):
    report: Literal["kam-activity-summary"]
    generated_at: datetime
    params: KamActivityParamsOut
    period: KamActivityPeriod
    previous_period: PreviousPeriod
    scope: KamActivityScope
    kpi: KamActivityKpi
    highlights: list[str] = Field(description="≤5 детерминированных выводов (тот же текст в PDF)")
    stages: dict[str, list[StageConversionRow]]
    dynamics: KamActivityDynamics
    by_kam: list[KamBreakdownRow]
    top_stuck: list[StuckRequestRow]
    untouched_assigned: list[str] = Field(description="Закреплённые вузы без взаимодействий")
    talent: KamActivityTalent
    universities_by_stage: UniversitiesByStage | None = Field(
        default=None, description="Вузы B2B по этапам на конец периода; null — B2B вне отчёта"
    )
    risks: list[SummaryRisk] = Field(
        default_factory=list, description="Риски для заказчика: только ненулевые"
    )


# --- «Взаимодействия за период» (ТЗ ФТ4) --------------------------------------------------


class InteractionsParamsOut(KamActivityParamsOut):
    product_id: list[str] = Field(default_factory=list)
    program_id: list[str] = Field(default_factory=list)
    stage_id: list[str] = Field(default_factory=list)


class InteractionsColumn(_ApiModel):
    key: str
    label: str
    default: bool = Field(description="Колонка выбрана по умолчанию")
    group: str = Field(description="«ТЗ», «Даты» или «Дополнительно»")


class InteractionsStageOption(_ApiModel):
    id: str
    code: str
    name: str
    color: str | None = None
    workflow_type: WorkflowType
    is_terminal: bool


class InteractionsStage(_ApiModel):
    id: str
    code: str
    name: str = Field(description="Этап на конец периода; удалённый — «(удалён)»")
    color: str | None = None
    is_terminal: bool
    outcome: Outcome | None = None
    deleted: bool = False


class InteractionsCounts(_ApiModel):
    transitions: int
    returns: int
    comments: int
    files: int
    total: int = Field(description="Переходы + комментарии + файлы ответственного КАМа")


class InteractionsRow(_ApiModel):
    request_id: str
    title: str
    workflow_type: WorkflowType
    university_id: str | None = None
    university: str | None = Field(default=None, description="Наименование вуза (B2B)")
    university_short: str | None = None
    client: str | None = Field(default=None, description="Псевдоним B2C-клиента")
    it_direction: str | None = Field(
        default=None,
        description="ИТ-направление: направление программы заявки, без него — направления "
        "её ИТ-продукта через запятую",
    )
    product_id: str | None = None
    product: str | None = Field(default=None, description="ИТ-продукт")
    program_id: str | None = None
    program: str | None = None
    stage: InteractionsStage
    responsible_id: str
    responsible: str
    created_at: datetime
    stage_since: datetime
    days_on_stage: int
    threshold_days: int
    is_stuck: bool
    closed_in_period: Outcome | None = None
    interactions: InteractionsCounts
    last_contact_at: datetime | None = None
    amount: str | None = None


class InteractionsTotals(_ApiModel):
    requests: int
    universities: int
    responsibles: int
    with_interactions: int
    interactions: int
    stuck: int
    won: int
    lost: int
    amount: str


class InteractionsReportOut(_ApiModel):
    report: Literal["interactions"]
    generated_at: datetime
    params: InteractionsParamsOut
    period: KamActivityPeriod
    scope: KamActivityScope
    columns: list[InteractionsColumn] = Field(description="Каталог колонок таблицы и выгрузки")
    default_columns: list[str]
    stages: list[InteractionsStageOption] = Field(description="Этапы для фильтра «Статус»")
    totals: InteractionsTotals
    truncated: bool = Field(description="Строк больше 5000 — полный список в выгрузке")
    rows: list[InteractionsRow]
