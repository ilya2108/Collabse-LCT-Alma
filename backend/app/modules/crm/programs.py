"""Программы и ручное ранжирование приоритетов (api-contract.md §3.4, Р-12).

`priority_rank`: **меньше = выше**. Изменения приоритета пишутся в audit_log
(`action='program.priority_changed'`, before/after — старый/новый rank).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.cache import get_cache
from app.core.db import get_session
from app.core.errors import validation_error
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import get_current_user, require_roles
from app.models.crm import Product, Program, University
from app.models.service import ExternalLink
from app.models.user import AppUser
from app.modules.crm.deps import check_version, get_or_404
from app.modules.crm.schemas import (
    PriorityPatch,
    ProgramCreate,
    ProgramOut,
    ProgramUpdate,
    ReorderBody,
)

router = APIRouter(tags=["programs"])

_SORT_FIELDS = {
    "priority_rank": Program.priority_rank,
    "priority": Program.priority_rank,
    "name": Program.name,
    "starts_on": Program.starts_on,
    "created_at": Program.created_at,
    "updated_at": Program.updated_at,
}


async def _external_ids(
    session: AsyncSession, program_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, str]]:
    """cms_external_id / lms_course_id — read-only проекция external_link."""
    if not program_ids:
        return {}
    rows = (
        await session.execute(
            select(ExternalLink).where(
                ExternalLink.entity_type == "program",
                ExternalLink.entity_id.in_(program_ids),
            )
        )
    ).scalars()
    result: dict[uuid.UUID, dict[str, str]] = {}
    for link in rows:
        result.setdefault(link.entity_id, {})[link.system] = link.external_id
    return result


def _to_out(program: Program, links: dict[str, str] | None = None) -> ProgramOut:
    out = ProgramOut.model_validate(program)
    if links:
        out.cms_external_id = links.get("cms")
        out.lms_course_id = links.get("lms")
    return out


async def _validate_refs(session: AsyncSession, payload: dict[str, Any]) -> None:
    if payload.get("product_id") is not None:
        await get_or_404(session, Product, payload["product_id"], "Продукт не найден")
    if payload.get("university_id") is not None:
        await get_or_404(session, University, payload["university_id"], "Вуз не найден")


@router.get("/programs", summary="Программы (сортировка по приоритету: меньше = выше)")
async def list_programs(
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
    params: Annotated[ListParams, Depends(list_params)],
    product_id: Annotated[uuid.UUID | None, Query()] = None,
    university_id: Annotated[uuid.UUID | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Program)
    if product_id:
        stmt = stmt.where(Program.product_id == product_id)
    if university_id:
        stmt = stmt.where(Program.university_id == university_id)
    if is_active is not None:
        stmt = stmt.where(Program.is_active.is_(is_active))
    if search:
        stmt = stmt.where(Program.name.ilike(f"%{search.strip()}%"))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(stmt, params.sort, _SORT_FIELDS, default="priority_rank")
    rows = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    )
    links = await _external_ids(session, [row.id for row in rows])
    items = [
        _to_out(row, links.get(row.id)).model_dump(mode="json") for row in rows
    ]
    return list_envelope(items, total, params)


@router.post("/programs", status_code=201, summary="Создать программу")
async def create_program(
    body: ProgramCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> ProgramOut:
    payload = body.model_dump()
    await _validate_refs(session, payload)
    program = Program(**payload)
    session.add(program)
    await session.commit()
    await get_cache().delete("dict:programs")
    return _to_out(program)


@router.get("/programs/{program_id}", summary="Карточка программы")
async def get_program(
    program_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(get_current_user)],
) -> ProgramOut:
    program = await get_or_404(session, Program, program_id, "Программа не найдена")
    links = await _external_ids(session, [program_id])
    return _to_out(program, links.get(program_id))


@router.patch("/programs/{program_id}", summary="Изменить программу")
async def update_program(
    program_id: uuid.UUID,
    body: ProgramUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
) -> ProgramOut:
    program = await get_or_404(session, Program, program_id, "Программа не найдена")
    check_version(program, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version"})
    await _validate_refs(session, payload)
    for attr, value in payload.items():
        setattr(program, attr, value)
    await session.commit()
    await get_cache().delete("dict:programs")
    links = await _external_ids(session, [program_id])
    return _to_out(program, links.get(program_id))


@router.delete("/programs/{program_id}", summary="Деактивировать программу")
async def delete_program(
    program_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _user: Annotated[AppUser, Depends(require_roles("admin"))],
) -> dict[str, bool]:
    program = await get_or_404(session, Program, program_id, "Программа не найдена")
    program.is_active = False
    await session.commit()
    await get_cache().delete("dict:programs")
    return {"ok": True}


@router.patch(
    "/programs/{program_id}/priority",
    summary="Точечное изменение приоритета",
)
async def patch_priority(
    program_id: uuid.UUID,
    body: PriorityPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> ProgramOut:
    program = await get_or_404(session, Program, program_id, "Программа не найдена")
    check_version(program, body.version)
    old_rank = program.priority_rank
    program.priority_rank = body.priority_rank
    program.priority_updated_by = user.id
    program.priority_updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="program.priority_changed",
        entity_type="program",
        entity_id=program.id,
        before={"priority_rank": old_rank},
        after={"priority_rank": body.priority_rank},
        ip=client_ip(request),
    )
    await session.commit()
    await get_cache().delete("dict:programs")
    return _to_out(program)


@router.post(
    "/programs/priorities/reorder",
    summary="Массовое переупорядочивание (drag&drop)",
)
async def reorder_priorities(
    body: ReorderBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam", "kam"))],
    request: Request,
) -> dict[str, int]:
    ids = [item.id for item in body.items]
    if len(ids) != len(set(ids)):
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "items",
                    "code": "duplicate_id",
                    "message": "Одна и та же программа указана дважды",
                }
            ],
        )
    programs = {
        p.id: p
        for p in (await session.execute(select(Program).where(Program.id.in_(ids)))).scalars()
    }
    missing = set(ids) - set(programs)
    if missing:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "items",
                    "code": "lookup_not_found",
                    "message": f"Программы не найдены: {', '.join(str(m) for m in missing)}",
                }
            ],
        )
    now = datetime.now(timezone.utc)
    changed = 0
    for item in body.items:
        program = programs[item.id]
        if program.priority_rank == item.priority_rank:
            continue
        await write_audit(
            session,
            actor_user_id=user.id,
            action="program.priority_changed",
            entity_type="program",
            entity_id=program.id,
            before={"priority_rank": program.priority_rank},
            after={"priority_rank": item.priority_rank},
            ip=client_ip(request),
        )
        program.priority_rank = item.priority_rank
        program.priority_updated_by = user.id
        program.priority_updated_at = now
        changed += 1
    await session.commit()
    await get_cache().delete("dict:programs")
    return {"updated": changed}
