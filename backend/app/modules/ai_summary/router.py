"""`POST /reports/ai-summaries` и `GET /reports/ai-summaries/{job_id}` — «Сводка ИИ»
в отчёте «Активность КАМов» (api-contract.md §7.2.3).

Обе ручки — право `reports:ai_summary` и фичефлаг `ai_summaries` (выключен — 422
`unprocessable`). Область — `scope.ai_scope`; 403 на процесс вне области и чужого КАМа
при «только свои», 404 на неизвестного КАМа — от самих отчётов. Новых кодов ошибок нет:
сбои GigaChat — это `status: "failed"` задачи с `error.code` (не HTTP).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.user import AppUser
from app.modules.ai_summary import service
from app.modules.ai_summary.schemas import AiSummaryCreate, AiSummaryJobOut
from app.modules.ai_summary.scope import require_ai_enabled

router = APIRouter(prefix="/reports", tags=["reports"])

_FLAG_422 = {
    "description": "Сводки ИИ выключены администратором (фичефлаг `ai_summaries`) — `unprocessable`"
}


@router.post(
    "/ai-summaries",
    summary="Сводка ИИ по отчёту КАМов: по команде или по КАМу",
    response_model=None,
    responses={
        200: {
            "model": AiSummaryJobOut,
            "description": "Готовая сводка из кэша (1 ч), присоединение к сводке в полёте "
            "(`reused: true`) или сводка по правилам (`mode: rules`)",
        },
        202: {"model": AiSummaryJobOut, "description": "Задача поставлена: опрашивать GET"},
        400: {
            "description": "Тело не по схеме; период: from > to, длиннее 731 дня, начало в "
            "будущем; > 50 id; нет `kam_id` при `kind: kam` — `validation_error`"
        },
        403: {
            "description": "Нет права «Сводки ИИ по отчёту КАМов», процесс вне области или "
            "чужой КАМ при области «только свои»"
        },
        404: {"description": "КАМ не найден"},
        422: _FLAG_422,
    },
)
async def create_ai_summary(
    request: Request,
    response: Response,
    body: AiSummaryCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_ai_enabled)],
) -> dict[str, Any]:
    """Сводка фактов поверх цифр «Сводки для заказчика» и «Просрочек»: GigaChat получает
    только агрегаты, очищенные названия этапов и псевдонимы `КАМ-01…` (без ФИО, вузов,
    клиентов, заголовков заявок и комментариев); ФИО подставляются при чтении. Каждая
    фраза ответа сверяется с цифрами отчёта, советы вычищаются. `mode: rules` — сводка по
    правилам без ИИ, сразу."""
    payload, status = await service.create_job(session, user, body, request)
    response.status_code = status
    return payload


@router.get(
    "/ai-summaries/{job_id}",
    summary="Сводка ИИ: статус и результат задачи",
    response_model=None,
    responses={
        200: {"model": AiSummaryJobOut},
        400: {"description": "`job_id` — не UUID — `validation_error`"},
        404: {"description": "Задачи нет или она вне области пользователя"},
        422: _FLAG_422,
    },
)
async def get_ai_summary(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_ai_enabled)],
) -> dict[str, Any]:
    """Опрос раз в `poll_after_ms`, пока статус `queued`/`running`. `queue_ahead` — сколько
    сводок GigaChat перед этой (шлюз обрабатывает по одной)."""
    return await service.get_job(session, user, job_id)
