"""`GET /reports/sla` и `GET /reports/sla/kams/{kam_id}` — вкладка «Просрочки».

Отдельный роутер, как у «Взаимодействий за период»: префикс тот же (`/reports`),
подключается в `main.py`. Параметры — те же, что у отчёта «Активность КАМов»
(`kam_activity_params`: последние 30 дней МСК по умолчанию). Доступ — право
`reports:read`, область — `report_scope`, явные 403 — `kam_activity._resolve_scope`.
Выгрузка — `POST /export/jobs {entity_type: "report:sla"}` (XLSX, PDF, CSV/JSON).
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
from app.modules.reporting.kam_activity import MAX_FILTER_IDS, KamActivityParams
from app.modules.reporting.router import kam_activity_params
from app.modules.reporting.sla import build_sla, build_sla_kam
from app.modules.reporting.sla_schemas import SlaKamReportOut, SlaReportOut

router = APIRouter(prefix="/reports", tags=["reports"])

_IDS = f"повторяемый параметр (не больше {MAX_FILTER_IDS})"


def kam_view_params(
    date_from: Annotated[
        date | None,
        Query(alias="from", description="Начало периода (МСК); по умолчанию — 30 дней назад"),
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Конец периода включительно (МСК)")
    ] = None,
    workflow_type: Annotated[Literal["b2b", "b2c"] | None, Query()] = None,
    university_id: Annotated[list[uuid.UUID] | None, Query(description=f"Вуз, {_IDS}")] = None,
    direction_id: Annotated[
        list[uuid.UUID] | None, Query(description=f"ИТ-направление, {_IDS}")
    ] = None,
) -> KamActivityParams:
    """Параметры разбора КАМа — те же, что у отчёта, без `kam_id` (КАМ — в пути)."""
    return KamActivityParams(
        date_from=date_from,
        date_to=date_to,
        workflow_type=workflow_type,
        university_ids=tuple(university_id or ()),
        direction_ids=tuple(direction_id or ()),
    )


_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Период: from > to, длиннее 731 дня, начало в будущем; > 50 id"},
    403: {"description": "Процесс вне области роли или чужой kam_id при области «только свои»"},
}


# response_model=None: ответ отдаётся как собран (и как лежит в кэше), схема — в OpenAPI
@router.get(
    "/sla",
    summary="Просрочки: заявки дольше нормы этапа, «Уложились в норму», старение, тренд",
    response_model=None,
    responses={200: {"model": SlaReportOut}, **_ERRORS},
)
async def report_sla(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("reports:read"))],
    params: Annotated[KamActivityParams, Depends(kam_activity_params)],
) -> dict[str, Any]:
    """Просроченные заявки на дату среза (то же, что «зависшие»), задержка сверх нормы,
    корзины старения, этапы, «Уложились в норму» по моменту решения, нагрузка, тренд по
    неделям, КАМы и правило «Просрочек больше, чем по команде, или старше 30 дней».
    Атрибуция — на дату (кто вёл заявку), фильтр `kam_id` — тоже. Только факты."""
    return await build_sla(session, user, params)


@router.get(
    "/sla/kams/{kam_id}",
    summary="Просрочки: разбор КАМа",
    response_model=None,
    responses={
        200: {"model": SlaKamReportOut},
        **_ERRORS,
        404: {"description": "Пользователь не найден"},
    },
)
async def report_sla_kam(
    kam_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_permission("reports:read"))],
    params: Annotated[KamActivityParams, Depends(kam_view_params)],
) -> dict[str, Any]:
    """Просроченные заявки КАМа с последним действием, старение, «Уложились в норму» по
    этапам, тренд, опоздание закрытых этапов и напоминания с реакцией; справочно — значения
    команды (при области «только свои» — null). `kam_id` из query не участвует."""
    return await build_sla_kam(session, user, kam_id, params)
