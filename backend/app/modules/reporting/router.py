"""JSON-агрегаты для дашбордов ECharts (api-contract.md §7.2).

PNG-экспорт графиков — обязанность фронта (`getDataURL`); файловый экспорт данных
отчётов — `POST /export/jobs {entity_type: "report:<code>"}`. Ответы кэшируются
в KeyDB 60 с (`crm:v1:dash:*`), недоступный KeyDB деградирует в чтение из PG.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import get_current_user
from app.models.user import AppUser
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
        user: Annotated[AppUser, Depends(get_current_user)],
        params: Annotated[ReportParams, Depends(report_params)],
    ) -> dict[str, Any]:
        return await build_report(session, code, user, params)

    handler.__name__ = f"report_{code.replace('-', '_')}"
    return handler


for _code, _summary in _REPORTS:
    router.get(f"/{_code}", summary=_summary)(_make_handler(_code))
