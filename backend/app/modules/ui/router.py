"""UI-инфраструктура: SSE realtime, пресеты, глобальный поиск, фичефлаги.

Контракт — api-contract.md §2.2: кадр `event: <topic>\\ndata: <json>`, топики
workflow.changed / request.transitioned / request.comment_added / telegram.linked /
flags.updated / notification.created; fan-out между репликами — KeyDB pub/sub
`crm:v1:events` (app.core.sse). Поиск — до 5 записей в группе, студенты ищутся
по псевдонимизированному `display_name` (ПДн не раскрываются).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.db import get_session
from app.core.security import get_current_user
from app.core.sse import get_sse_hub
from app.models.crm import Contract, University
from app.models.deal import Deal
from app.models.talent import Student
from app.models.user import AppUser
from app.models.workflow import WorkflowStage
from app.modules.admin.service import get_flags
from app.modules.ui import presets as presets_service

router = APIRouter(tags=["ui"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Depends(get_current_user)

SEARCH_GROUP_LIMIT = 5


async def _event_frames(request: Request) -> AsyncIterator[dict[str, Any]]:
    hub = get_sse_hub()
    queue = hub.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                continue  # тишина: sse-starlette сам шлёт ping-кадры
            yield {
                "event": frame["topic"],
                "data": orjson.dumps(frame["data"]).decode("utf-8"),
            }
    finally:
        hub.unsubscribe(queue)


@router.get("/events/stream", summary="SSE realtime-событий UI", dependencies=[CurrentUser])
async def events_stream(request: Request) -> EventSourceResponse:
    return EventSourceResponse(_event_frames(request), ping=15)


# --------------------------------------------------------------------------------------
# Пресеты таблиц/фильтров (только свои)
# --------------------------------------------------------------------------------------


class PresetIn(BaseModel):
    screen: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    is_default: bool = False
    state: dict[str, Any] = Field(default_factory=dict)


class PresetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_default: bool | None = None
    state: dict[str, Any] | None = None


@router.get("/ui/presets", summary="Пресеты таблиц/фильтров текущего пользователя")
async def list_presets(
    session: SessionDep,
    user: Annotated[AppUser, Depends(get_current_user)],
    screen: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    items = presets_service.filter_presets(
        await presets_service.load_items(session, user), screen
    )
    return {"items": items, "total": len(items)}


@router.post("/ui/presets", status_code=201, summary="Создать пресет")
async def create_preset(
    body: PresetIn,
    session: SessionDep,
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    items = await presets_service.load_items(session, user)
    preset = presets_service.add_preset(items, body.model_dump())
    await presets_service.save_items(session, user, items)
    return preset


@router.patch("/ui/presets/{preset_id}", summary="Изменить пресет")
async def update_preset(
    preset_id: str,
    body: PresetPatch,
    session: SessionDep,
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    items = await presets_service.load_items(session, user)
    preset = presets_service.patch_preset(
        items, preset_id, body.model_dump(exclude_unset=True)
    )
    await presets_service.save_items(session, user, items)
    return preset


@router.delete("/ui/presets/{preset_id}", status_code=204, summary="Удалить пресет")
async def delete_preset(
    preset_id: str,
    session: SessionDep,
    user: Annotated[AppUser, Depends(get_current_user)],
) -> None:
    items = await presets_service.load_items(session, user)
    remaining = presets_service.remove_preset(items, preset_id)
    await presets_service.save_items(session, user, remaining)


# --------------------------------------------------------------------------------------
# Глобальный поиск Cmd+K (§2.2)
# --------------------------------------------------------------------------------------


@router.get("/search", summary="Глобальный поиск (Cmd+K)", dependencies=[CurrentUser])
async def global_search(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
) -> dict[str, Any]:
    needle = f"%{q.strip()}%"

    universities = (
        (
            await session.execute(
                select(University)
                .where(
                    University.is_active.is_(True),
                    or_(
                        University.name.ilike(needle),
                        University.short_name.ilike(needle),
                        University.inn.ilike(needle),
                        University.city.ilike(needle),
                    ),
                )
                .order_by(University.name)
                .limit(SEARCH_GROUP_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    deals = (
        await session.execute(
            select(Deal, WorkflowStage)
            .join(WorkflowStage, WorkflowStage.id == Deal.stage_id)
            .where(Deal.status != "archived", Deal.title.ilike(needle))
            .order_by(Deal.updated_at.desc())
            .limit(SEARCH_GROUP_LIMIT)
        )
    ).all()

    students = (
        (
            await session.execute(
                select(Student)
                .where(Student.display_name.ilike(needle))
                .order_by(Student.display_name)
                .limit(SEARCH_GROUP_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    contracts = (
        (
            await session.execute(
                select(Contract)
                .where(Contract.number.ilike(needle))
                .order_by(Contract.number)
                .limit(SEARCH_GROUP_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    return {
        "universities": [
            {
                "id": str(u.id),
                "name": u.name,
                "short_name": u.short_name,
                "region": u.region,
                "city": u.city,
            }
            for u in universities
        ],
        "requests": [
            {
                "id": str(d.id),
                "title": d.title,
                "workflow_type": d.pipeline,
                "status": {"id": str(s.id), "name": s.name, "color": s.color},
            }
            for d, s in deals
        ],
        "students": [
            {
                "id": str(st.id),
                "display_name": st.display_name,
                "funnel_status": st.funnel_status,
            }
            for st in students
        ],
        "contracts": [
            {
                "id": str(c.id),
                "number": c.number,
                "status": c.status,
                "university_id": str(c.university_id) if c.university_id else None,
            }
            for c in contracts
        ],
    }


# --------------------------------------------------------------------------------------
# Фичефлаги для UI (§2.2); управление — /admin/feature-flags
# --------------------------------------------------------------------------------------


@router.get("/flags", summary="Активные фичефлаги", dependencies=[CurrentUser])
async def flags(session: SessionDep) -> dict[str, bool]:
    return await get_flags(session)
