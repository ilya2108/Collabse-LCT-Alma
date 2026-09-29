"""Доменная логика Talent Pool: ПДн, воронка студента, сериализация.

data-model.md §7–8, api-contract.md §8. Все выдачи маскированы для всех ролей;
открытые значения — только POST /students/{id}/reveal с аудитом (Р-19).
Воронка: candidate → studying → graduate → talent_pool; вперёд — только на
соседний статус, назад — на любой, но с обязательным комментарием (§8.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import (
    display_name_from_full_name,
    get_pii_crypto,
    mask_email,
    mask_full_name,
    mask_phone,
)
from app.models.deal import Deal
from app.models.service import FileObject
from app.models.talent import Student, StudentStatusHistory
from app.models.user import AppUser

FUNNEL_ORDER: tuple[str, ...] = ("candidate", "studying", "graduate", "talent_pool")

FUNNEL_LABELS = {
    "candidate": "Кандидат",
    "studying": "Обучается",
    "graduate": "Выпускник",
    "talent_pool": "Пул талантов",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------
# Воронка: чистая валидация перехода (юнит-тестируется без БД)
# --------------------------------------------------------------------------------------


def validate_funnel_transition(
    from_status: str, to_status: str, comment: str | None
) -> str | None:
    """None — переход допустим; иначе код ошибки.

    Правила §8.2: соседний вперёд; любой назад — с комментарием (исправление
    ошибок/отчисление); прыжки вперёд и переход «в себя» запрещены.
    """
    if from_status == to_status:
        return "same_status"
    try:
        from_idx = FUNNEL_ORDER.index(from_status)
        to_idx = FUNNEL_ORDER.index(to_status)
    except ValueError:
        return "unknown_status"
    if to_idx > from_idx + 1:
        return "forward_jump"
    if to_idx < from_idx and not (comment or "").strip():
        return "comment_required"
    return None


TRANSITION_ERROR_MESSAGES = {
    "same_status": "Студент уже находится в этом статусе",
    "unknown_status": "Неизвестный статус воронки",
    "forward_jump": "Вперёд по воронке можно переходить только на соседний статус",
    "comment_required": "Возврат назад по воронке требует комментария",
}


# --------------------------------------------------------------------------------------
# ПДн
# --------------------------------------------------------------------------------------


def apply_student_pii(student: Student, *, full_name: str, email: str) -> None:
    """Проставляет открытые значения + blind-индексы + псевдоним (шифрует EncryptedStr)."""
    crypto = get_pii_crypto()
    student.full_name = full_name.strip()
    student.email = email.strip()
    student.email_hmac = crypto.email_hmac(email)
    student.full_name_hmac = crypto.full_name_hmac(full_name)
    student.display_name = display_name_from_full_name(full_name)


async def find_student_by_email(session: AsyncSession, email: str) -> Student | None:
    """Дедупликация по blind index `email_hmac` (data-model.md §7.1)."""
    email_hmac = get_pii_crypto().email_hmac(email)
    return (
        await session.execute(select(Student).where(Student.email_hmac == email_hmac))
    ).scalar_one_or_none()


# --------------------------------------------------------------------------------------
# Сериализация (ПДн всегда маскированы)
# --------------------------------------------------------------------------------------


def serialize_student(
    student: Student,
    *,
    files_count: int = 0,
    b2c_request_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(student.id),
        "display_name": student.display_name,
        "full_name": mask_full_name(student.full_name),
        "email": mask_email(student.email),
        "phone": mask_phone(student.phone) if student.phone else None,
        "university_id": str(student.university_id) if student.university_id else None,
        "product_id": str(student.product_id) if student.product_id else None,
        "program_id": str(student.program_id) if student.program_id else None,
        "counterparty_id": str(student.counterparty_id) if student.counterparty_id else None,
        "federal_project_id": (
            str(student.federal_project_id) if student.federal_project_id else None
        ),
        "b2c_request_id": str(b2c_request_id) if b2c_request_id else None,
        "funnel_status": student.funnel_status,
        "status_changed_at": student.status_changed_at.isoformat()
        if student.status_changed_at
        else None,
        "source": student.source,
        "notes": student.notes,
        "files_count": files_count,
        "version": student.xmin,
        "created_at": student.created_at.isoformat() if student.created_at else None,
        "updated_at": student.updated_at.isoformat() if student.updated_at else None,
    }


async def files_count_map(
    session: AsyncSession, student_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not student_ids:
        return {}
    rows = await session.execute(
        select(FileObject.entity_id, func.count())
        .where(
            FileObject.entity_type == "student",
            FileObject.entity_id.in_(student_ids),
            FileObject.deleted_at.is_(None),
        )
        .group_by(FileObject.entity_id)
    )
    return {entity_id: int(count) for entity_id, count in rows.all()}


async def latest_b2c_request_id(
    session: AsyncSession, counterparty_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Производное поле §8.2: последняя B2C-заявка контрагента студента."""
    if counterparty_id is None:
        return None
    return (
        await session.execute(
            select(Deal.id)
            .where(
                Deal.counterparty_id == counterparty_id,
                Deal.pipeline == "b2c",
                Deal.status != "archived",
            )
            .order_by(Deal.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def serialize_history_row(
    row: StudentStatusHistory, users: dict[uuid.UUID, AppUser]
) -> dict[str, Any]:
    actor = users.get(row.actor_user_id) if row.actor_user_id else None
    return {
        "id": str(row.id),
        "from_status": row.from_status,
        "to_status": row.to_status,
        "actor": (
            {"id": str(actor.id), "full_name": actor.full_name or actor.username}
            if actor
            else None
        ),
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def record_status_change(
    session: AsyncSession,
    student: Student,
    *,
    to_status: str,
    actor_id: uuid.UUID | None,
    reason: str | None,
) -> None:
    """Смена статуса + строка истории в текущей транзакции (commit — у вызывающего)."""
    session.add(
        StudentStatusHistory(
            student_id=student.id,
            from_status=student.funnel_status,
            to_status=to_status,
            actor_user_id=actor_id,
            reason=reason,
        )
    )
    student.funnel_status = to_status
    student.status_changed_at = _now()
