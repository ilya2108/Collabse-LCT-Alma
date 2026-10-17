"""Схемы API «Сводки ИИ» (`/reports/ai-summaries`, api-contract.md §7.2.3)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.reporting.kam_activity import MAX_FILTER_IDS
from app.modules.reporting.schemas import KamActivityParamsOut

JobKind = Literal["team", "kam"]
JobMode = Literal["auto", "rules"]
JobStatus = Literal["queued", "running", "done", "failed"]
JobProvider = Literal["gigachat", "rules", "stub"]
ErrorCode = Literal[
    "llm_unavailable",
    "llm_busy",
    "llm_timeout",
    "llm_not_configured",
    "llm_budget_exceeded",
    "llm_bad_output",
    "interrupted",
    "rate_limited",
    "disabled",
    "internal",
]

_IDS = f"не больше {MAX_FILTER_IDS}"


class AiSummaryParams(BaseModel):
    """Фильтры отчёта — те же ключи и смысл, что у `GET /reports/kam-activity`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date | None = Field(
        default=None, alias="from", description="Начало периода (МСК); по умолчанию — 30 дней"
    )
    to: date | None = Field(default=None, description="Конец периода включительно (МСК)")
    workflow_type: Literal["b2b", "b2c"] | None = None
    kam_id: list[uuid.UUID] = Field(
        default_factory=list, description=f"КАМы ({_IDS}); при kind=kam игнорируется"
    )
    university_id: list[uuid.UUID] = Field(default_factory=list, description=f"Вузы ({_IDS})")
    direction_id: list[uuid.UUID] = Field(
        default_factory=list, description=f"ИТ-направления ({_IDS})"
    )


class AiSummaryCreate(BaseModel):
    """Запрос сводки: по команде (`team`) или по КАМу (`kam`, нужен `kam_id`)."""

    model_config = ConfigDict(extra="forbid")

    kind: JobKind
    kam_id: uuid.UUID | None = Field(default=None, description="Обязателен при kind=kam")
    params: AiSummaryParams = Field(default_factory=AiSummaryParams)
    mode: JobMode = Field(
        default="auto",
        description="auto — провайдер из настроек (GigaChat); rules — сводка по правилам сразу",
    )
    force: bool = Field(default=False, description="«Обновить»: мимо кэша на час")


class AiSummarySections(BaseModel):
    headline: str = Field(description="Коротко: главный итог периода")
    going_well: list[str] = Field(default_factory=list, description="Что идёт хорошо")
    lagging: list[str] = Field(default_factory=list, description="Отставание и задержки")
    attention: list[str] = Field(
        default_factory=list,
        description="Кто требует внимания (по КАМу — где задержки)",
    )
    questions: list[str] = Field(default_factory=list, description="Вопросы для разбора")


class AiSummaryPerson(BaseModel):
    alias: str = Field(description="Псевдоним, который видела модель: «КАМ-03»")
    kam_id: uuid.UUID
    name: str = Field(description="«Фамилия И. О.» — подставлено на стороне CRM")


class AiSummaryRemoved(BaseModel):
    numbers: int = Field(default=0, description="Число или дата, которых нет в данных отчёта")
    advice: int = Field(default=0, description="Совет вместо факта")
    evaluative: int = Field(
        default=0, description="Оценка людей или работы; утверждение про КАМа без цифр"
    )
    other: int = Field(
        default=0, description="Неизвестный псевдоним, ссылка, вопрос без «?», длинная фраза"
    )


class AiSummaryChecks(BaseModel):
    numbers_total: int = Field(description="Чисел и дат во всех фразах ответа")
    numbers_matched: int = Field(
        description="Из них в оставшихся фразах (есть в данных отчёта; метрику проверка не "
        "различает — сверяйте цифры)"
    )
    sentences_removed: int = Field(description="Удалено фраз (число не совпало, совет и т. п.)")
    advice_removed: int = Field(description="Из них — с советами")
    removed_by_reason: AiSummaryRemoved = Field(
        default_factory=AiSummaryRemoved, description="Удалённые фразы по причинам"
    )


class AiSummaryResult(BaseModel):
    source: JobProvider = Field(description="Фактический источник: gigachat | rules | stub")
    model: str | None = Field(default=None, description="Модель GigaChat; null для rules")
    generated_at: datetime
    sections: AiSummarySections = Field(description="ФИО уже подставлены вместо псевдонимов")
    people: list[AiSummaryPerson] = Field(
        default_factory=list, description="Упомянутые КАМы (только kind=team)"
    )
    checks: AiSummaryChecks


class AiSummaryError(BaseModel):
    code: ErrorCode
    message: str
    rules_available: bool = Field(default=True, description="Сводка по правилам доступна")


class AiSummaryJobOut(BaseModel):
    """Задача сводки (ответ обеих ручек)."""

    id: uuid.UUID
    kind: JobKind
    kam_id: uuid.UUID | None = None
    kam_name: str | None = Field(default=None, description="«Фамилия И. О.» при kind=kam")
    status: JobStatus
    mode: JobMode
    provider: JobProvider = Field(description="Запланированный провайдер")
    params: KamActivityParamsOut = Field(description="Эхо фильтров после подстановки периода")
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue_ahead: int | None = Field(
        default=None,
        description="Сводок GigaChat в очереди перед этой (только queued/running)",
    )
    poll_after_ms: int | None = Field(
        default=None, description="2000 для queued/running — когда опросить снова"
    )
    reused: bool = Field(description="Ответ — уже существующая задача (кэш или в полёте)")
    result: AiSummaryResult | None = None
    error: AiSummaryError | None = None
