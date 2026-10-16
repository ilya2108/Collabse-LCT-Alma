"""JSON-агрегаты для дашбордов ECharts (api-contract.md §7.2) и отчёт «Активность КАМов» (§7.2.1).

PNG-экспорт графиков — обязанность фронта (`getDataURL`); файловый экспорт данных
отчётов — `POST /export/jobs {entity_type: "report:<code>"}` (для `report:kam-activity` —
многолистовой XLSX и PDF для заказчика). Ответы кэшируются в KeyDB 60 с
(`crm:v1:dash:*`), недоступный KeyDB деградирует в чтение из PG.
Доступ — право `reports:read`; область (процессы роли, «по всем КАМам» только с
`reports:all`) — `service.report_scope`. Отчёт КАМов проверяет область явно: процесс
вне области и чужой `kam_id` при области «только свои» — 403 (`kam_activity._resolve_scope`).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_permission
from app.models.user import AppUser
from app.modules.reporting.kam_activity import (
    MAX_FILTER_IDS,
    KamActivityParams,
    build_kam_activity,
    build_kam_activity_summary,
)
from app.modules.reporting.schemas import KamActivityReportOut, KamActivitySummaryOut
from app.modules.reporting.service import ReportParams, build_report

router = APIRouter(prefix="/reports", tags=["reports"])


def report_params(
    workflow_type: Annotated[Literal["b2b", "b2c"] | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    kam_id: Annotated[uuid.UUID | None, Query()] = None,
    product_id: Annotated[uuid.UUID | None, Query()] = None,
    university_id: Annotated[uuid.UUID | None, Query()] = None,
    program_id: Annotated[uuid.UUID | None, Query()] = None,
    region: Annotated[str | None, Query()] = None,
    federal_project: Annotated[uuid.UUID | None, Query()] = None,
    threshold_days: Annotated[int | None, Query(ge=1, le=60)] = None,
) -> ReportParams:
    return ReportParams(
        workflow_type=workflow_type,
        date_from=date_from,
        date_to=date_to,
        kam_id=kam_id,
        product_id=product_id,
        university_id=university_id,
        program_id=program_id,
        region=region,
        federal_project_id=federal_project,
        threshold_days=threshold_days,
    )


_REPORTS: tuple[tuple[str, str], ...] = (
    ("funnel", "Воронка по статусам workflow (counts + суммы)"),
    ("kam-workload", "Нагрузка КАМов: активные заявки, вузы, зависшие"),
    ("stuck", "Зависшие заявки (детализация, порог каскадом)"),
    ("universities-coverage", "Вузы по регионам: договор / в работе / без контакта"),
    ("programs-demand", "Программы по ручному приоритету + число заявок"),
    ("talent-pool-funnel", "Воронка студентов: кандидат → talent pool"),
    ("dynamics", "Динамика создания/закрытия заявок по неделям"),
    ("dashboard", "Агрегат всех виджетов дашборда одним запросом"),
)


def _make_handler(code: str):
    async def handler(
        session: Annotated[AsyncSession, Depends(get_session)],
        user: Annotated[AppUser, Depends(require_permission("reports:read"))],
        params: Annotated[ReportParams, Depends(report_params)],
    ) -> dict[str, Any]:
        return await build_report(session, code, user, params)

    handler.__name__ = f"report_{code.replace('-', '_')}"
    return handler


for _code, _summary in _REPORTS:
    router.get(f"/{_code}", summary=_summary)(_make_handler(_code))


# --- отчёт «Активность КАМов» (§7.2.1) --------------------------------------------------


def kam_activity_params(
    date_from: Annotated[
        date | None,
        Query(alias="from", description="Начало периода (МСК); по умолчанию — 30 дней назад"),
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Конец периода включительно (МСК)")
    ] = None,
    workflow_type: Annotated[Literal["b2b", "b2c"] | None, Query()] = None,
    kam_id: Annotated[
        list[uuid.UUID] | None,
        Query(description=f"КАМ, повторяемый параметр (не больше {MAX_FILTER_IDS})"),
    ] = None,
    university_id: Annotated[
        list[uuid.UUID] | None,
        Query(description=f"Вуз, повторяемый параметр (не больше {MAX_FILTER_IDS})"),
    ] = None,
) -> KamActivityParams:
    # период и число id проверяет resolve_params (400 period_* / too_many_values)
    return KamActivityParams(
        date_from=date_from,
        date_to=date_to,
        workflow_type=workflow_type,
        kam_ids=tuple(kam_id or ()),
        university_ids=tuple(university_id or ()),
    )


_KAM_ACTIVITY_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Период: from > to, длиннее 731 дня, начало в будущем; > 50 id"},
    403: {"description": "Процесс вне области роли или чужой kam_id при области «только свои»"},
}


# response_model=None: ответ отдаётся как собран (и как лежит в кэше), схема — в OpenAPI
@router.get(
    "/kam-activity",
    summary="Активность КАМов: КАМ → вузы → заявки за период",
    response_model=None,
    responses={200: {"model": KamActivityReportOut}, **_KAM_ACTIVITY_ERRORS},
)
async def report_kam_activity(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("reports:read"))],
    params: Annotated[KamActivityParams, Depends(kam_activity_params)],
) -> dict[str, Any]:
    """Детализация для руководителя КАМов: этап на конец периода (по истории переходов),
    дни на этапе, собственные взаимодействия КАМа, закреплённые вузы без контакта."""
    return await build_kam_activity(session, user, params)


@router.get(
    "/kam-activity/summary",
    summary="Сводка активности КАМов для заказчика (сравнение с прошлым периодом)",
    response_model=None,
    responses={200: {"model": KamActivitySummaryOut}, **_KAM_ACTIVITY_ERRORS},
)
async def report_kam_activity_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("reports:read"))],
    params: Annotated[KamActivityParams, Depends(kam_activity_params)],
) -> dict[str, Any]:
    """KPI с дельтами к прошлому периоду той же длины, конверсия по этапам, где застревают,
    динамика взаимодействий, разрез по КАМам, топ зависших и текстовые выводы."""
    return await build_kam_activity_summary(session, user, params)
