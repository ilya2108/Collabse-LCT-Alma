"""Talent Pool: студенты, воронка, федпроекты, активности, расписание (data-model.md §7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.pii import EncryptedStr
from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk


class FederalProject(CreatedAtMixin, VersionedMixin, Base):
    __tablename__ = "federal_project"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    code: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)
    starts_on: Mapped[date | None] = mapped_column(sa.Date)
    ends_on: Mapped[date | None] = mapped_column(sa.Date)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )


class Student(TimestampMixin, VersionedMixin, Base):
    """ПДн минимальны (ФИО + email), в БД — только Fernet-шифртекст и HMAC-хэши.

    Атрибуты `full_name`/`email`/`phone` в Python — открытые значения (EncryptedStr),
    в API по умолчанию всегда маскируются; открытые — только POST /students/{id}/reveal.
    """

    __tablename__ = "student"

    id: Mapped[uuid.UUID] = uuid_pk()
    full_name: Mapped[str] = mapped_column("full_name_enc", EncryptedStr, nullable=False)
    email: Mapped[str] = mapped_column("email_enc", EncryptedStr, nullable=False)
    phone: Mapped[str | None] = mapped_column("phone_enc", EncryptedStr)
    email_hmac: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    full_name_hmac: Mapped[str | None] = mapped_column(sa.CHAR(64))
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)  # «Фамилия И.О.»
    university_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("university.id"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("product.id"))
    program_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("program.id"))
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("counterparty.id")
    )  # связь с B2C-воронкой (kind='person')
    federal_project_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("federal_project.id")
    )
    funnel_status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'candidate'"), default="candidate"
    )  # candidate | studying | graduate | talent_pool
    status_changed_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    source: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'manual'"), default="manual"
    )  # manual | import | lms
    import_session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("import_session.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))

    status_history: Mapped[list[StudentStatusHistory]] = relationship(
        back_populates="student", cascade="all, delete-orphan", passive_deletes=True
    )


class StudentStatusHistory(CreatedAtMixin, Base):
    __tablename__ = "student_status_history"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("student.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(sa.Text)
    to_status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id")
    )  # NULL = система (LMS-событие, импорт)
    reason: Mapped[str | None] = mapped_column(sa.Text)

    student: Mapped[Student] = relationship(back_populates="status_history")


class Activity(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "activity"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    activity_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'other'"), default="other"
    )  # course | event | internship | hackathon | other
    federal_project_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("federal_project.id", ondelete="SET NULL")
    )
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("university.id", ondelete="SET NULL")
    )
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("program.id", ondelete="SET NULL")
    )
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    description: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )

    schedule: Mapped[list[ActivitySchedule]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", passive_deletes=True
    )


class ActivitySchedule(CreatedAtMixin, Base):
    """Сквозное расписание = единый календарь всех слотов (индекс по starts_at)."""

    __tablename__ = "activity_schedule"

    id: Mapped[uuid.UUID] = uuid_pk()
    activity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("activity.id", ondelete="CASCADE"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    location: Mapped[str | None] = mapped_column(sa.Text)
    format: Mapped[str | None] = mapped_column(sa.Text)  # online | offline | hybrid
    note: Mapped[str | None] = mapped_column(sa.Text)

    activity: Mapped[Activity] = relationship(back_populates="schedule")


class StudentActivity(Base):
    __tablename__ = "student_activity"

    student_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("student.id", ondelete="CASCADE"), primary_key=True
    )
    activity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("activity.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'registered'"), default="registered"
    )  # registered | in_progress | completed | dropped
    enrolled_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


__all__ = [
    "FederalProject",
    "Student",
    "StudentStatusHistory",
    "Activity",
    "ActivitySchedule",
    "StudentActivity",
]
