"""Заявки: `deal` (API — /requests, UI — «заявка»), история, комментарии (data-model.md §6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.workflow import WorkflowStage


class Deal(TimestampMixin, VersionedMixin, Base):
    """Терминологический мост (OVERVIEW.md §2.1): таблица `deal` ↔ ресурс API `Request`."""

    __tablename__ = "deal"

    id: Mapped[uuid.UUID] = uuid_pk()
    pipeline: Mapped[str] = mapped_column(sa.Text, nullable=False)  # b2b | b2c
    workflow_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("workflow.id"), nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stage.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    university_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("university.id"))
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("counterparty.id"))
    interaction_type_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("interaction_type.id")
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("contract.id"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("product.id"))
    program_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("program.id"))
    responsible_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id"), nullable=False
    )
    amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    currency: Mapped[str] = mapped_column(
        sa.CHAR(3), nullable=False, server_default=sa.text("'RUB'"), default="RUB"
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'open'"), default="open"
    )  # open | won | lost | archived
    # база расчёта «зависших»; API — status_updated_at
    stage_entered_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    source: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'manual'"), default="manual"
    )  # manual | import | cms | lms
    description: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))

    stage: Mapped[WorkflowStage] = relationship("WorkflowStage", foreign_keys=[stage_id])
    history: Mapped[list[DealStageHistory]] = relationship(
        back_populates="deal", cascade="all, delete-orphan", passive_deletes=True
    )
    comments: Mapped[list[DealComment]] = relationship(
        back_populates="deal", cascade="all, delete-orphan", passive_deletes=True
    )


class DealStageHistory(CreatedAtMixin, Base):
    """Audit переходов; имена этапов — снапшоты, история не переписывается."""

    __tablename__ = "deal_stage_history"

    id: Mapped[uuid.UUID] = uuid_pk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("deal.id", ondelete="CASCADE"), nullable=False
    )
    from_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_stage.id")
    )  # NULL = создание заявки
    to_stage_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workflow_stage.id"), nullable=False
    )
    from_stage_name: Mapped[str | None] = mapped_column(sa.Text)
    to_stage_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    transition_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_transition.id")
    )
    is_return: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id")
    )  # NULL = система
    kind: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'manual'"), default="manual"
    )  # manual | system_migration | integration | import
    reason: Mapped[str | None] = mapped_column(sa.Text)
    comment: Mapped[str | None] = mapped_column(sa.Text)

    deal: Mapped[Deal] = relationship(back_populates="history")


class DealComment(CreatedAtMixin, Base):
    """Встроенный мессенджер — лента комментариев заявки (заглушка по решению)."""

    __tablename__ = "deal_comment"

    id: Mapped[uuid.UUID] = uuid_pk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("deal.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id"), nullable=False
    )
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    deal: Mapped[Deal] = relationship(back_populates="comments")


__all__ = ["Deal", "DealStageHistory", "DealComment"]
