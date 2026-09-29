"""Talent Pool: студенты, воронка, файлы, активности (api-contract.md §8).

Все выдачи ПДн маскированы; раскрытие — только POST /students/{id}/reveal
с аудитом `student.pii_revealed` (observer — 403, Р-19).

Резолюция (data-model приоритетнее api-contract): в DDL `student` нет `deleted_at`,
поэтому DELETE /students/{id} выполняет право на забвение немедленно — физическое
удаление записи (история и участия каскадом), файлы — soft-delete до purge-джобы.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import client_ip, write_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import conflict, not_found, validation_error
from app.core.events import EVENT_STUDENT_TALENT_POOL_ADDED
from app.core.outbox import emit_event_notifications
from app.core.pagination import ListParams, apply_sort, list_envelope, list_params
from app.core.security import get_current_user, require_roles
from app.models.crm import Program, University
from app.models.service import FileObject
from app.models.talent import (
    Activity,
    ActivitySchedule,
    FederalProject,
    Student,
    StudentActivity,
    StudentStatusHistory,
)
from app.models.user import AppUser
from app.modules.crm.deps import check_version, get_or_404
from app.modules.crm.schemas import FileOut
from app.modules.files.service import soft_delete_file, store_upload
from app.modules.talent_pool import service as talent
from app.modules.talent_pool.schemas import (
    ActivityCreate,
    ActivityOut,
    ActivityUpdate,
    ParticipantsBody,
    StudentCreate,
    StudentRevealOut,
    StudentStatusBody,
    StudentUpdate,
)

router = APIRouter(tags=["talent-pool"])

_READ = Depends(get_current_user)
_WRITE_ROLES = ("admin", "head_kam", "kam")

_STUDENT_SORT = {
    "display_name": Student.display_name,
    "funnel_status": Student.funnel_status,
    "status_changed_at": Student.status_changed_at,
    "created_at": Student.created_at,
    "updated_at": Student.updated_at,
}


async def _load_student(session: AsyncSession, student_id: uuid.UUID) -> Student:
    student = await session.get(Student, student_id)
    if student is None:
        raise not_found("Студент не найден")
    return student


# --------------------------------------------------------------------------------------
# Студенты
# --------------------------------------------------------------------------------------


@router.get("/students", summary="Студенты (ПДн маскированы)", dependencies=[_READ])
async def list_students(
    session: Annotated[AsyncSession, Depends(get_session)],
    params: Annotated[ListParams, Depends(list_params)],
    search: Annotated[
        str | None, Query(description="По display_name; точный email — через blind index")
    ] = None,
    university_id: Annotated[uuid.UUID | None, Query()] = None,
    program_id: Annotated[uuid.UUID | None, Query()] = None,
    funnel_status: Annotated[str | None, Query()] = None,
    federal_project_id: Annotated[uuid.UUID | None, Query()] = None,
    activity_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Student)
    if search:
        needle = search.strip()
        if "@" in needle:
            # поиск по точному email — HMAC blind index, ПДн не расшифровываются
            from app.core.pii import get_pii_crypto

            stmt = stmt.where(Student.email_hmac == get_pii_crypto().email_hmac(needle))
        else:
            stmt = stmt.where(Student.display_name.ilike(f"%{needle}%"))
    if university_id:
        stmt = stmt.where(Student.university_id == university_id)
    if program_id:
        stmt = stmt.where(Student.program_id == program_id)
    if funnel_status:
        if funnel_status not in talent.FUNNEL_ORDER:
            raise validation_error(
                "Ошибка валидации данных",
                details=[
                    {
                        "field": "funnel_status",
                        "code": "invalid_value",
                        "message": f"Допустимые статусы: {', '.join(talent.FUNNEL_ORDER)}",
                    }
                ],
            )
        stmt = stmt.where(Student.funnel_status == funnel_status)
    if federal_project_id:
        stmt = stmt.where(Student.federal_project_id == federal_project_id)
    if activity_id:
        stmt = stmt.where(
            select(StudentActivity.student_id)
            .where(
                StudentActivity.activity_id == activity_id,
                StudentActivity.student_id == Student.id,
            )
            .exists()
        )
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    stmt = apply_sort(stmt, params.sort, _STUDENT_SORT, default="display_name")
    students = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    )
    counts = await talent.files_count_map(session, [s.id for s in students])
    items = [
        talent.serialize_student(s, files_count=counts.get(s.id, 0)) for s in students
    ]
    return list_envelope(items, total, params)


@router.post("/students", status_code=201, summary="Создать студента")
async def create_student(
    body: StudentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
    request: Request,
) -> dict[str, Any]:
    existing = await talent.find_student_by_email(session, body.email)
    if existing is not None:
        raise conflict(
            f"Студент с таким email уже существует: {existing.display_name}"
        )
    student = Student(
        phone=body.phone,
        university_id=body.university_id,
        product_id=body.product_id,
        program_id=body.program_id,
        counterparty_id=body.counterparty_id,
        federal_project_id=body.federal_project_id,
        funnel_status=body.funnel_status,
        notes=body.notes,
        source="manual",
        created_by=user.id,
    )
    talent.apply_student_pii(student, full_name=body.full_name, email=body.email)
    session.add(student)
    await session.flush()
    session.add(
        StudentStatusHistory(
            student_id=student.id,
            from_status=None,
            to_status=body.funnel_status,
            actor_user_id=user.id,
        )
    )
    await write_audit(
        session,
        actor_user_id=user.id,
        action="student.created",
        entity_type="student",
        entity_id=student.id,
        after={"display_name": student.display_name},  # ПДн в аудит не пишутся
        ip=client_ip(request),
    )
    await session.commit()
    return talent.serialize_student(student)


@router.get("/students/{student_id}", summary="Карточка студента", dependencies=[_READ])
async def get_student(
    student_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(get_current_user)],
) -> dict[str, Any]:
    student = await _load_student(session, student_id)
    counts = await talent.files_count_map(session, [student.id])
    b2c_request_id = await talent.latest_b2c_request_id(session, student.counterparty_id)
    out = talent.serialize_student(
        student, files_count=counts.get(student.id, 0), b2c_request_id=b2c_request_id
    )

    # денормализованные подписи связей для карточки (frontend Student.university/…)
    async def _ref(model: type, ref_id: uuid.UUID | None) -> dict[str, str] | None:
        if ref_id is None:
            return None
        row = await session.get(model, ref_id)
        return {"id": str(row.id), "name": row.name} if row is not None else None

    out["university"] = await _ref(University, student.university_id)
    out["program"] = await _ref(Program, student.program_id)
    out["federal_project"] = await _ref(FederalProject, student.federal_project_id)

    is_observer_only = "observer" in (user.roles or []) and not (
        set(_WRITE_ROLES) & set(user.roles or [])
    )
    files: list[dict[str, Any]] = []
    if not is_observer_only:  # файлы студентов observer'у недоступны (§8.3)
        rows = (
            (
                await session.execute(
                    select(FileObject)
                    .where(
                        FileObject.entity_type == "student",
                        FileObject.entity_id == student_id,
                        FileObject.deleted_at.is_(None),
                    )
                    .order_by(FileObject.created_at)
                )
            )
            .scalars()
            .all()
        )
        files = [FileOut.model_validate(f).model_dump(mode="json") for f in rows]
    out["files"] = files

    activities = (
        await session.execute(
            select(StudentActivity, Activity)
            .join(Activity, Activity.id == StudentActivity.activity_id)
            .where(StudentActivity.student_id == student_id)
            .order_by(StudentActivity.enrolled_at)
        )
    ).all()
    out["activities"] = [
        {
            "activity_id": str(activity.id),
            "name": activity.name,
            "activity_type": activity.activity_type,
            "status": link.status,
            "enrolled_at": link.enrolled_at.isoformat() if link.enrolled_at else None,
        }
        for link, activity in activities
    ]

    history_rows = (
        (
            await session.execute(
                select(StudentStatusHistory)
                .where(StudentStatusHistory.student_id == student_id)
                .order_by(StudentStatusHistory.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    users = await _users_map(session, [r.actor_user_id for r in history_rows])
    out["history"] = [talent.serialize_history_row(r, users) for r in history_rows]
    return out


async def _users_map(
    session: AsyncSession, ids: list[uuid.UUID | None]
) -> dict[uuid.UUID, AppUser]:
    wanted = [i for i in ids if i is not None]
    if not wanted:
        return {}
    rows = (await session.execute(select(AppUser).where(AppUser.id.in_(wanted)))).scalars()
    return {u.id: u for u in rows}


@router.post(
    "/students/{student_id}/reveal",
    summary="Раскрыть ПДн (с аудитом; observer — 403)",
)
async def reveal_student(
    student_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
    request: Request,
) -> StudentRevealOut:
    student = await _load_student(session, student_id)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="student.pii_revealed",
        entity_type="student",
        entity_id=student.id,
        after={"display_name": student.display_name},  # кто/когда/кого — без самих ПДн
        ip=client_ip(request),
    )
    await session.commit()
    return StudentRevealOut(
        id=student.id,
        full_name=student.full_name,
        email=student.email,
        phone=student.phone,
    )


@router.patch("/students/{student_id}", summary="Изменить студента")
async def update_student(
    student_id: uuid.UUID,
    body: StudentUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
) -> dict[str, Any]:
    student = await _load_student(session, student_id)
    check_version(student, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version"})

    new_email = payload.pop("email", None)
    new_full_name = payload.pop("full_name", None)
    if new_email:
        duplicate = await talent.find_student_by_email(session, new_email)
        if duplicate is not None and duplicate.id != student.id:
            raise conflict("Студент с таким email уже существует")
    if new_email or new_full_name:
        talent.apply_student_pii(
            student,
            full_name=new_full_name or student.full_name,
            email=new_email or student.email,
        )
    for attr, value in payload.items():
        setattr(student, attr, value)
    await session.commit()
    return talent.serialize_student(student)


@router.delete(
    "/students/{student_id}",
    summary="Право на забвение 152-ФЗ (физическое удаление)",
)
async def delete_student(
    student_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin"))],
    request: Request,
) -> dict[str, bool]:
    student = await _load_student(session, student_id)
    display_name = student.display_name
    files = (
        (
            await session.execute(
                select(FileObject).where(
                    FileObject.entity_type == "student",
                    FileObject.entity_id == student_id,
                    FileObject.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for file_object in files:
        soft_delete_file(file_object)  # объекты в MinIO чистит purge-джоба
    await session.delete(student)  # история и участия — каскадом (DDL §7)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="student.deleted",
        entity_type="student",
        entity_id=student_id,
        before={"display_name": display_name},
        ip=client_ip(request),
    )
    await session.commit()
    return {"ok": True}


@router.post("/students/{student_id}/status", summary="Переход по воронке")
async def change_student_status(
    student_id: uuid.UUID,
    body: StudentStatusBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
) -> dict[str, Any]:
    student = await _load_student(session, student_id)
    check_version(student, body.version)
    error = talent.validate_funnel_transition(student.funnel_status, body.to, body.comment)
    if error == "comment_required":
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "comment",
                    "code": "required",
                    "message": talent.TRANSITION_ERROR_MESSAGES[error],
                }
            ],
        )
    if error is not None:
        raise conflict(talent.TRANSITION_ERROR_MESSAGES[error])
    talent.record_status_change(
        session, student, to_status=body.to, actor_id=user.id, reason=body.comment
    )
    if body.to == "talent_pool":
        # событие по правилам нотификаций (сид: role head_kam, email); ПДн — только псевдоним
        await emit_event_notifications(
            session,
            event_type=EVENT_STUDENT_TALENT_POOL_ADDED,
            entity_type="student",
            entity_id=student.id,
            subject="Студент в кадровом резерве",
            body=f"Студент {student.display_name} добавлен в talent pool",
            payload={"student_id": str(student.id), "display_name": student.display_name},
        )
    await session.commit()
    return talent.serialize_student(student)


@router.get(
    "/students/{student_id}/history", summary="История воронки", dependencies=[_READ]
)
async def student_history(
    student_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await _load_student(session, student_id)
    rows = (
        (
            await session.execute(
                select(StudentStatusHistory)
                .where(StudentStatusHistory.student_id == student_id)
                .order_by(StudentStatusHistory.created_at)
            )
        )
        .scalars()
        .all()
    )
    users = await _users_map(session, [r.actor_user_id for r in rows])
    items = [talent.serialize_history_row(r, users) for r in rows]
    return {"items": items, "total": len(items)}


# --- файлы студента (observer — 403 на все операции, §8.3) ------------------------------


@router.post(
    "/students/{student_id}/files",
    status_code=201,
    summary="Загрузить файл студента (резюме, сертификат)",
)
async def upload_student_file(
    student_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="До 50 МБ")],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
) -> FileOut:
    await _load_student(session, student_id)
    file_object = await store_upload(
        session,
        upload=file,
        bucket=get_settings().s3_bucket_files,
        entity_type="student",
        entity_id=student_id,
        uploaded_by=user.id,
    )
    await session.commit()
    return FileOut.model_validate(file_object)


@router.get(
    "/students/{student_id}/files",
    summary="Файлы студента (observer — 403)",
    dependencies=[Depends(require_roles(*_WRITE_ROLES))],
)
async def list_student_files(
    student_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[FileOut]:
    await _load_student(session, student_id)
    rows = (
        (
            await session.execute(
                select(FileObject)
                .where(
                    FileObject.entity_type == "student",
                    FileObject.entity_id == student_id,
                    FileObject.deleted_at.is_(None),
                )
                .order_by(FileObject.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [FileOut.model_validate(f) for f in rows]


@router.delete(
    "/students/{student_id}/files/{file_id}",
    summary="Удалить файл студента",
    dependencies=[Depends(require_roles("admin", "head_kam"))],
)
async def delete_student_file(
    student_id: uuid.UUID,
    file_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    await _load_student(session, student_id)
    file_object = await get_or_404(session, FileObject, file_id, "Файл не найден")
    if file_object.entity_type != "student" or file_object.entity_id != student_id:
        raise not_found("Файл не найден")
    soft_delete_file(file_object)
    await session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------------------
# Активности и сквозное расписание
# --------------------------------------------------------------------------------------


async def _participants_map(
    session: AsyncSession, activity_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not activity_ids:
        return {}
    rows = await session.execute(
        select(StudentActivity.activity_id, func.count())
        .where(StudentActivity.activity_id.in_(activity_ids))
        .group_by(StudentActivity.activity_id)
    )
    return {activity_id: int(count) for activity_id, count in rows.all()}


def _activity_out(
    activity: Activity,
    participants: int,
    federal_project: dict[str, str] | None = None,
) -> ActivityOut:
    out = ActivityOut.model_validate(activity)
    out.schedule = sorted(out.schedule, key=lambda slot: slot.starts_at)
    out.participants_count = participants
    out.federal_project = federal_project
    return out


async def _federal_project_refs(
    session: AsyncSession, activities: list[Activity]
) -> dict[uuid.UUID, dict[str, str]]:
    ids = {a.federal_project_id for a in activities if a.federal_project_id}
    if not ids:
        return {}
    rows = (
        (await session.execute(select(FederalProject).where(FederalProject.id.in_(ids))))
        .scalars()
        .all()
    )
    return {fp.id: {"id": str(fp.id), "name": fp.name} for fp in rows}


@router.get(
    "/activities", summary="Сквозное расписание активностей", dependencies=[_READ]
)
async def list_activities(
    session: Annotated[AsyncSession, Depends(get_session)],
    params: Annotated[ListParams, Depends(list_params)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    kind: Annotated[str | None, Query(description="Тип активности")] = None,
    federal_project: Annotated[uuid.UUID | None, Query()] = None,
    university_id: Annotated[uuid.UUID | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Activity).options(selectinload(Activity.schedule))
    if kind:
        stmt = stmt.where(Activity.activity_type == kind)
    if federal_project:
        stmt = stmt.where(Activity.federal_project_id == federal_project)
    if university_id:
        stmt = stmt.where(Activity.university_id == university_id)
    if is_active is not None:
        stmt = stmt.where(Activity.is_active.is_(is_active))
    if date_from or date_to:
        slot = select(ActivitySchedule.id).where(
            ActivitySchedule.activity_id == Activity.id
        )
        if date_from:
            slot = slot.where(ActivitySchedule.starts_at >= date_from)
        if date_to:
            slot = slot.where(ActivitySchedule.starts_at < date_to + timedelta(days=1))
        stmt = stmt.where(slot.exists())
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    # сквозной календарь: сортировка по ближайшему слоту (data-model.md §7.3)
    next_slot = (
        select(func.min(ActivitySchedule.starts_at))
        .where(ActivitySchedule.activity_id == Activity.id)
        .scalar_subquery()
    )
    stmt = stmt.order_by(sa.nulls_last(next_slot.asc()), Activity.name)
    activities = (
        (await session.execute(stmt.limit(params.limit).offset(params.offset)))
        .scalars()
        .all()
    )
    participants = await _participants_map(session, [a.id for a in activities])
    fp_refs = await _federal_project_refs(session, list(activities))
    items = [
        _activity_out(
            a, participants.get(a.id, 0), fp_refs.get(a.federal_project_id)
        ).model_dump(mode="json")
        for a in activities
    ]
    return list_envelope(items, total, params)


@router.post(
    "/activities",
    status_code=201,
    summary="Создать активность",
)
async def create_activity(
    body: ActivityCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam"))],
) -> ActivityOut:
    activity = Activity(**body.model_dump(exclude={"schedule"}))
    for slot in body.schedule:
        activity.schedule.append(ActivitySchedule(**slot.model_dump()))
    session.add(activity)
    await session.commit()
    await session.refresh(activity, attribute_names=["schedule"])
    return _activity_out(activity, 0)


@router.patch("/activities/{activity_id}", summary="Изменить активность")
async def update_activity(
    activity_id: uuid.UUID,
    body: ActivityUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam"))],
) -> ActivityOut:
    activity = (
        await session.execute(
            select(Activity)
            .options(selectinload(Activity.schedule))
            .where(Activity.id == activity_id)
        )
    ).scalar_one_or_none()
    if activity is None:
        raise not_found("Активность не найдена")
    check_version(activity, body.version)
    payload = body.model_dump(exclude_unset=True, exclude={"version", "schedule"})
    for attr, value in payload.items():
        setattr(activity, attr, value)
    if body.schedule is not None:
        activity.schedule.clear()
        for slot in body.schedule:
            activity.schedule.append(ActivitySchedule(**slot.model_dump()))
    await session.commit()
    await session.refresh(activity, attribute_names=["schedule"])
    participants = await _participants_map(session, [activity.id])
    fp_refs = await _federal_project_refs(session, [activity])
    return _activity_out(
        activity, participants.get(activity.id, 0), fp_refs.get(activity.federal_project_id)
    )


@router.delete("/activities/{activity_id}", summary="Деактивировать активность")
async def delete_activity(
    activity_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles("admin", "head_kam"))],
) -> dict[str, bool]:
    activity = await get_or_404(session, Activity, activity_id, "Активность не найдена")
    # деактивация вместо удаления: на активность ссылаются участия студентов
    activity.is_active = False
    await session.commit()
    return {"ok": True}


@router.post(
    "/activities/{activity_id}/participants",
    status_code=201,
    summary="Записать студентов на активность",
)
async def add_participants(
    activity_id: uuid.UUID,
    body: ParticipantsBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
) -> dict[str, Any]:
    await get_or_404(session, Activity, activity_id, "Активность не найдена")
    found = (
        (
            await session.execute(
                select(Student.id).where(Student.id.in_(body.student_ids))
            )
        )
        .scalars()
        .all()
    )
    missing = set(body.student_ids) - set(found)
    if missing:
        raise validation_error(
            "Ошибка валидации данных",
            details=[
                {
                    "field": "student_ids",
                    "code": "lookup_not_found",
                    "message": f"Студенты не найдены: {', '.join(str(m) for m in missing)}",
                }
            ],
        )
    inserted = 0
    for student_id in found:
        result = await session.execute(
            pg_insert(StudentActivity)
            .values(student_id=student_id, activity_id=activity_id)
            .on_conflict_do_nothing()
            .returning(StudentActivity.student_id)
        )
        if result.scalar_one_or_none() is not None:
            inserted += 1
    await session.commit()
    return {"added": inserted, "already_enrolled": len(found) - inserted}


@router.delete(
    "/activities/{activity_id}/participants/{student_id}",
    summary="Отписать студента от активности",
)
async def remove_participant(
    activity_id: uuid.UUID,
    student_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[AppUser, Depends(require_roles(*_WRITE_ROLES))],
) -> dict[str, bool]:
    link = await session.get(StudentActivity, (student_id, activity_id))
    if link is None:
        raise not_found("Студент не записан на эту активность")
    await session.delete(link)
    await session.commit()
    return {"ok": True}
