"""`GET /reports/interactions` — «Взаимодействия за период» по колонкам ТЗ (ФТ4).

Отдельный роутер, чтобы не пересекаться с `reporting/router.py`; префикс тот же
(`/reports`), подключается в `main.py`. Доступ — право `reports:read`, область — общая
область отчётов (`report_scope`), явные 403 — как у отчёта «Активность КАМов».
Выгрузка — `POST /export/jobs {entity_type: "report:interactions", columns: [...]}`.
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
from app.modules.reporting.interactions import InteractionsParams, build_interactions
from app.modules.reporting.kam_activity import MAX_FILTER_IDS, KamActivityParams
from app.modules.reporting.schemas import InteractionsReportOut

router = APIRouter(prefix="/reports", tags=["reports"])

_IDS = f"повторяемый параметр (не больше {MAX_FILTER_IDS})"


def interactions_params(
    date_from: Annotated[
        date | None,
        Query(alias="from", description="Начало периода (МСК); по умолчанию — 30 дней назад"),
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Конец периода включительно (МСК)")
    ] = None,
    workflow_type: Annotated[Literal["b2b", "b2c"] | None, Query()] = None,
    kam_id: Annotated[list[uuid.UUID] | None, Query(description=f"КАМ, {_IDS}")] = None,
    university_id: Annotated[list[uuid.UUID] | None, Query(description=f"Вуз, {_IDS}")] = None,
    direction_id: Annotated[
        list[uuid.UUID] | None,
        Query(description=f"ИТ-направление (программы заявки, без него — её продукта), {_IDS}"),
    ] = None,
    product_id: Annotated[list[uuid.UUID] | None, Query(description=f"ИТ-продукт, {_IDS}")] = None,
    program_id: Annotated[list[uuid.UUID] | None, Query(description=f"Программа, {_IDS}")] = None,
    stage_id: Annotated[
        list[uuid.UUID] | None,
        Query(description=f"Статус работы с вузом — этап на конец периода, {_IDS}"),
    ] = None,
) -> InteractionsParams:
    # период и число id проверяет сервис (400 period_* / too_many_values)
    return InteractionsParams(
        base=KamActivityParams(
            date_from=date_from,
            date_to=date_to,
            workflow_type=workflow_type,
            kam_ids=tuple(kam_id or ()),
            university_ids=tuple(university_id or ()),
            direction_ids=tuple(direction_id or ()),
        ),
        product_ids=tuple(product_id or ()),
        program_ids=tuple(program_id or ()),
        stage_ids=tuple(stage_id or ()),
    )


@router.get(
    "/interactions",
    summary="Взаимодействия с вузами за период: таблица по колонкам ТЗ",
    response_model=None,
    responses={
        200: {"model": InteractionsReportOut},
        400: {"description": "Период: from > to, длиннее 731 дня, начало в будущем; > 50 id"},
        403: {"description": "Процесс вне области роли или чужой kam_id при области «свои»"},
    },
)
async def report_interactions(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("reports:read"))],
    params: Annotated[InteractionsParams, Depends(interactions_params)],
) -> dict[str, Any]:
    """Строка — заявка, с которой шла работа в периоде (та же семантика периода, что у
    «Активности КАМов»): вуз, ИТ-направление, ИТ-продукт, программа, статус работы с вузом
    (этап на конец периода), ответственный, даты. Фильтры — вузы, ИТ-направления,
    продукты, программы, КАМы, статус. Каталог колонок и колонки по умолчанию — в ответе
    (`columns`)."""
    return await build_interactions(session, user, params)
