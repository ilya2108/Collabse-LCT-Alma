"""Workflow как данные: воронки, этапы, переходы, пакеты изменений (data-model.md §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk


class Workflow(TimestampMixin, VersionedMixin, Base):
    """Ровно две строки: b2b и b2c. Версионирования нет — одна живая схема."""

    __tablename__ = "workflow"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)  # 'b2b' | 'b2c'
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    stuck_threshold_days: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("14"), default=14
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))

    stages: Mapped[list[WorkflowStage]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", passive_deletes=True
    )
    transitions: Mapped[list[WorkflowTransition]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="WorkflowTransition.workflow_id",
    )


class WorkflowStage(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "workflow_stage"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    is_initial: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    is_terminal: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    terminal_outcome: Mapped[str | None] = mapped_column(sa.Text)  # won | lost
    stuck_threshold_days: Mapped[int | None] = mapped_column(sa.Integer)
    # вход в этап шлёт crm.request.handed_over в LMS
    triggers_lms_handover: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    color: Mapped[str | None] = mapped_column(sa.Text)
    ui_position: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {"x": .., "y": ..}
    deleted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    workflow: Mapped[Workflow] = relationship(back_populates="stages")


class WorkflowTransition(CreatedAtMixin, VersionedMixin, Base):
    __tablename__ = "workflow_transition"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False
    )
    from_stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stage.id"), nullable=False
    )
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stage.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_return: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    requires_comment: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    allowed_roles: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'"), default=list
    )
    condition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # подсказка, не форсируется
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    deleted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    workflow: Mapped[Workflow] = relationship(
        back_populates="transitions", foreign_keys=[workflow_id]
    )
    from_stage: Mapped[WorkflowStage] = relationship(foreign_keys=[from_stage_id])
    to_stage: Mapped[WorkflowStage] = relationship(foreign_keys=[to_stage_id])


class WorkflowChangeRequest(Base):
    """Пакет операций изменения схемы с админ-апрувом («защита от дурака»)."""

    __tablename__ = "workflow_change_request"

    id: Mapped[uuid.UUID] = uuid_pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow.id", ondelete="CASCADE"), nullable=False
    )
    operations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'"), default="pending"
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("app_user.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(sa.Text)
    applied_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


__all__ = ["Workflow", "WorkflowStage", "WorkflowTransition", "WorkflowChangeRequest"]
