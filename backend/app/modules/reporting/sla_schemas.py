"""Pydantic-схемы ответов вкладки «Просрочки» (`/reports/sla`, api-contract.md §7.2.2).

Нужны для OpenAPI: форма совпадает с тем, что собирает `sla.py` (ответ отдаётся как
собран и как лежит в кэше, `response_model=None`). Моменты времени — ISO 8601 с зоной,
даты — ГГГГ-ММ-ДД (МСК), дни — с одним знаком, проценты — с одним знаком (дельта — п.п.).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.modules.reporting.schemas import (
    KamActivityParamsOut,
    KamActivityScope,
    PreviousPeriod,
    WorkflowType,
    _ApiModel,
)

BucketKey = Literal["d1_3", "d4_7", "d8_14", "d15_30", "d31_plus"]
NormSource = Literal["stage", "workflow", "global"]
Coverage = Literal["none", "partial", "full"]
EpisodeStatus = Literal["reacted_72h", "no_action", "pending", "undelivered"]


class SlaPeriod(_ApiModel):
    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    as_of: datetime = Field(description="Момент среза T = min(конец периода, сейчас)")
    days: int
    is_current: bool = Field(description="Период ещё идёт (срез — сейчас)")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SlaComparison(_ApiModel):
    snapshot_label: str = Field(description="«к 01.10» — остатки, срез на начало периода")
    period_label: str = Field(description="«к 15.09–30.09» — показатели за прошлый период")


class SlaKpiValue(_ApiModel):
    value: float | int | None = None
    prev: float | int | None = None
    delta: float | int | None = Field(default=None, description="Проценты — п.п., дни — 0,1")
    compare: Literal["snapshot", "period"]


class SlaKpi(_ApiModel):
    overdue: SlaKpiValue = Field(description="Просрочено на срез (то же, что «зависшие»)")
    active: SlaKpiValue = Field(description="В работе на срез")
    own_overdue: SlaKpiValue = Field(description="Собственные просрочки")
    inherited: SlaKpiValue = Field(description="Получены уже просроченными при передаче")
    overdue_pct: SlaKpiValue
    delay_median_days: SlaKpiValue = Field(description="«Сейчас сверх нормы» — медиана")
    delay_avg_days: SlaKpiValue
    delay_max_days: SlaKpiValue
    delay_total_days: SlaKpiValue = Field(description="Заявко-дней сверх нормы")
    overdue_30_plus: SlaKpiValue
    overdue_no_action: SlaKpiValue = Field(description="После срока без действий в системе")
    decided: SlaKpiValue = Field(description="Решённые в периоде этапы")
    on_time: SlaKpiValue
    on_time_pct: SlaKpiValue = Field(description="«Уложились в норму», %")
    completed: SlaKpiValue
    late_closed: SlaKpiValue
    late_delay_avg_days: SlaKpiValue = Field(description="«Опоздание закрытых этапов»")
    late_delay_median_days: SlaKpiValue
    inflow: SlaKpiValue = Field(description="Поступило за период: созданные и назначенные")


class SlaDefinition(_ApiModel):
    term: str
    text: str


class SlaTeamRef(_ApiModel):
    kams_total: int = Field(description="КАМы команды: активные, с заявками в работе на срез")
    active_median: float | int | None = None
    overdue_median: float | int | None = None


class SlaInactiveOwners(_ApiModel):
    users: int
    active: int
    overdue: int


class SlaAgingRow(_ApiModel):
    key: BucketKey
    label: str
    count: int
    prev: int | None = None


class SlaWorkflowSummary(_ApiModel):
    label: str = Field(description="«Вузы (B2B)» / «Клиенты (B2C)»")
    active: int
    overdue: int
    overdue_pct: float | None = None
    delay_median_days: float | None = None
    on_time_pct: float | None = None


class SlaStageRef(_ApiModel):
    id: str
    code: str
    name: str = Field(description="Мягко удалённый этап — с пометкой «(удалён)»")
    color: str | None = None
    deleted: bool = False


class SlaAging(_ApiModel):
    d1_3: int
    d4_7: int
    d8_14: int
    d15_30: int
    d31_plus: int


class SlaStageRow(SlaStageRef):
    sort_order: int
    norm_days: int
    norm_source: NormSource
    active: int
    overdue: int
    overdue_pct: float | None = None
    aging: SlaAging
    delay_median_days: float | None = None
    delay_avg_days: float | None = None
    delay_total_days: float | None = None
    decided: int
    on_time: int
    on_time_pct: float | None = None
    completed: int
    late_delay_avg_days: float | None = None


class SlaKamStageRow(SlaStageRow):
    workflow_type: WorkflowType


class SlaTrendWindow(_ApiModel):
    from_: date = Field(alias="from", serialization_alias="from")
    to: date

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SlaTrendPoint(_ApiModel):
    start: date
    end: date
    snapshot_at: datetime
    label: str = Field(description="Дата среза «23.08» (у месяцев — «на 30.09»)")
    complete: bool = Field(description="false — корзина не закончилась (рисуется пунктиром)")
    in_period: bool
    active: int
    overdue: int
    overdue_pct: float | None = Field(default=None, description="null при active < min_n")
    overdue_30_plus: int
    delay_avg_days: float | None = None
    decided: int
    on_time: int
    on_time_pct: float | None = Field(default=None, description="null при decided < min_n")


class SlaTrend(_ApiModel):
    granularity: Literal["week", "month"]
    window: SlaTrendWindow
    min_n: int
    points: list[SlaTrendPoint]


class SlaReminderStats(_ApiModel):
    visits_reminded: int
    visits_escalated: int
    sent: int
    delivered: int
    episodes: int
    matured: int
    reacted_72h: int
    reacted_72h_pct: float | None = None
    no_action: int
    pending: int
    undelivered: int
    reaction_median_hours: float | None = None
    reaction_avg_hours: float | None = None


class SlaRemindersInfo(_ApiModel):
    coverage: Coverage
    prev_coverage: Coverage
    journal_from: date | None = None
    check_enabled: bool
    disabled_since: date | None = None
    rules_active: bool
    note: str


class SlaKamPrev(_ApiModel):
    active: int
    overdue: int
    own_overdue: int
    overdue_pct: float | None = None
    on_time_pct: float | None = None
    delay_avg_days: float | None = None
    delay_median_days: float | None = None


class SlaKamDelta(_ApiModel):
    overdue: int | None = None
    overdue_pct: float | None = None
    on_time_pct: float | None = None
    delay_avg_days: float | None = None
    delay_median_days: float | None = None


class SlaKamRow(_ApiModel):
    kam_id: str
    kam: str
    is_active: bool
    roles: list[str]
    active: int
    overdue: int
    own_overdue: int
    inherited: int
    overdue_pct: float | None = None
    small_sample: bool = Field(description="В работе меньше 3 — доля показывается серым")
    delay_avg_days: float | None = None
    delay_median_days: float | None = None
    delay_max_days: float | None = None
    delay_total_days: float | None = None
    aging: SlaAging
    overdue_30_plus: int
    overdue_no_action: int
    decided: int
    on_time: int
    on_time_pct: float | None = None
    completed: int
    late_closed: int
    late_inherited: int
    late_delay_avg_days: float | None = None
    late_delay_median_days: float | None = None
    inflow: int
    reminders: SlaReminderStats | None = Field(
        default=None, description="null — журнал напоминаний пуст"
    )
    prev: SlaKamPrev | None = None
    delta: SlaKamDelta | None = None
    attention: bool


class SlaAttentionItem(_ApiModel):
    kam_id: str
    kam: str
    active: int
    overdue: int
    own_overdue: int
    inherited: int
    overdue_pct: float | None = None
    overdue_30_plus: int
    facts: list[str] = Field(description="До 6 строк-фактов, без оценок")


class SlaAttention(_ApiModel):
    rule: str = Field(description="Правило отбора — тот же текст в UI, XLSX и PDF")
    team_own_overdue_pct: float | None = None
    threshold_pct: float | None = None
    total_kams: int
    items: list[SlaAttentionItem]


class SlaUniversityRef(_ApiModel):
    id: str
    name: str
    short_name: str | None = None


class SlaLastAction(_ApiModel):
    at: datetime
    kind: str = Field(description="transition | created | comment | file")
    actor_id: str | None = None
    actor: str | None = None
    days_ago: float


class SlaOverdueItemOut(_ApiModel):
    request_id: str
    title: str
    workflow_type: WorkflowType
    university: SlaUniversityRef | None = None
    client: str | None = None
    kam_id: str
    kam: str
    stage: SlaStageRef = Field(description="Этап на момент среза")
    stage_since: datetime
    on_stage_days: float
    norm_days: float
    norm_source: NormSource
    delay_days: float
    bucket: BucketKey
    overdue_since: datetime | None = None
    no_action_after_due: bool
    inherited: bool
    with_kam_since: datetime | None = None
    reminders: int
    delivered: int
    escalated: bool
    last_action: SlaLastAction | None = None


class SlaOverdueList(_ApiModel):
    total: int
    truncated: bool = Field(description="Список обрезан предохранителем — полный в XLSX")
    items: list[SlaOverdueItemOut]


class SlaExitedTo(_ApiModel):
    name: str
    is_return: bool
    outcome: Literal["won", "lost"] | None = None


class SlaLateClosedItem(_ApiModel):
    request_id: str
    title: str
    workflow_type: WorkflowType | None = None
    kam_id: str | None = None
    kam: str | None = None
    university: SlaUniversityRef | None = None
    client: str | None = None
    stage: SlaStageRef
    entered: datetime
    left_at: datetime
    duration_days: float
    norm_days: float
    delay_days: float
    inherited: bool
    exited_to: SlaExitedTo


class SlaLateClosedList(_ApiModel):
    total: int
    items: list[SlaLateClosedItem]


class SlaReminderEpisode(_ApiModel):
    request_id: str
    title: str
    kam_id: str | None = None
    kam: str | None = None
    stage: SlaStageRef
    first_reminder_at: datetime
    delivered_at: datetime | None = None
    status: EpisodeStatus
    reaction_at: datetime | None = None
    reaction_kind: str | None = None
    reaction_hours: float | None = None


class SlaTeamReminders(_ApiModel):
    info: SlaRemindersInfo
    team: SlaReminderStats | None = Field(default=None, description="null — журнал пуст")
    items: list[SlaReminderEpisode] | None = Field(
        default=None, description="Эпизоды — только в выгрузке"
    )


class SlaReportOut(_ApiModel):
    report: Literal["sla"]
    generated_at: datetime
    params: KamActivityParamsOut
    period: SlaPeriod
    previous_period: PreviousPeriod
    comparison: SlaComparison
    scope: KamActivityScope
    headline: str = Field(description="Детерминированная строка-итог (A1 XLSX, PDF)")
    definition: str
    definitions: list[SlaDefinition] = Field(description="«Как считается»")
    kpi: SlaKpi
    team: SlaTeamRef
    inactive_owners: SlaInactiveOwners | None = None
    aging: list[SlaAgingRow]
    by_workflow: dict[WorkflowType, SlaWorkflowSummary]
    stages: dict[WorkflowType, list[SlaStageRow]]
    trend: SlaTrend
    kams: list[SlaKamRow]
    attention: SlaAttention
    overdue: SlaOverdueList
    reminders: SlaTeamReminders
    notes: list[str] = Field(description="Факты о данных: нормы, переводы, передачи, журнал")
    late_closed: SlaLateClosedList | None = Field(
        default=None, description="Опоздание закрытых этапов — только в выгрузке"
    )


class SlaKamTeamRef(_ApiModel):
    overdue_pct: float | None = None
    delay_median_days: float | None = None
    delay_avg_days: float | None = None
    on_time_pct: float | None = None
    active_median: float | int | None = None
    reacted_72h_pct: float | None = None
    reaction_median_hours: float | None = None


class SlaKamRef(_ApiModel):
    id: str
    name: str
    is_active: bool
    roles: list[str]


class SlaKamReminders(_ApiModel):
    info: SlaRemindersInfo
    visits_reminded: int | None = None
    visits_escalated: int | None = None
    sent: int | None = None
    delivered: int | None = None
    episodes: int | None = None
    matured: int | None = None
    reacted_72h: int | None = None
    reacted_72h_pct: float | None = None
    no_action: int | None = None
    pending: int | None = None
    undelivered: int | None = None
    reaction_median_hours: float | None = None
    reaction_avg_hours: float | None = None
    items: list[SlaReminderEpisode] | None = None
    prev: SlaReminderStats | None = Field(
        default=None, description="Прошлый период; дельта скрыта при prev_coverage ≠ full"
    )


class SlaKamReportOut(_ApiModel):
    report: Literal["sla-kam"]
    generated_at: datetime
    params: KamActivityParamsOut
    period: SlaPeriod
    previous_period: PreviousPeriod
    comparison: SlaComparison
    scope: KamActivityScope
    kam: SlaKamRef
    headline: str
    definition: str
    definitions: list[SlaDefinition]
    kpi: SlaKpi
    team: SlaKamTeamRef | None = Field(default=None, description="null при «только свои»")
    aging: list[SlaAgingRow]
    overdue: SlaOverdueList
    stages: list[SlaKamStageRow]
    trend: SlaTrend
    late_closed: SlaLateClosedList
    reminders: SlaKamReminders
    notes: list[str]
