"""Pydantic-схемы ответов отчёта «Активность КАМов» (api-contract.md §7.2.1).

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

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


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
    programs: list[TalentProgramRow]


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
