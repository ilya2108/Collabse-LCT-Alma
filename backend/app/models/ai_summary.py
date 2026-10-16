"""Задачи «Сводки ИИ» отчёта «Активность КАМов» (modules/ai_summary, миграция 0010).

Таблица, а не KeyDB: POST и опрос GET попадают в разные реплики и воркеры, KeyDB без
persistence и с вытеснением LRU; дедупликация задач в полёте — частичный UNIQUE по
`cache_key`, кэш на час переживает рестарты. В `facts` — ровно то, что ушло наружу
(агрегаты и псевдонимы КАМов, без ПДн), в `aliases` — `{"КАМ-01": "<uuid>"}`: ФИО в таблице
нет, они подставляются при чтении. Модель повторяет DDL миграции колонка в колонку.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, uuid_pk

JOB_KINDS = ("team", "kam")
JOB_MODES = ("auto", "rules")
JOB_PROVIDERS = ("gigachat", "rules", "stub")
JOB_STATUSES = ("queued", "running", "done", "failed")
PENDING_STATUSES = ("queued", "running")


class AiSummaryJob(CreatedAtMixin, Base):
    """Задача сводки: факты (что ушло наружу), статус, результат с псевдонимами."""

    __tablename__ = "ai_summary_job"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('team','kam')", name="kind"),
        sa.CheckConstraint("mode IN ('auto','rules')", name="mode"),
        sa.CheckConstraint("provider IN ('gigachat','rules','stub')", name="provider"),
        sa.CheckConstraint("status IN ('queued','running','done','failed')", name="status"),
        sa.CheckConstraint("(kind = 'kam') = (kam_id IS NOT NULL)", name="kam"),
        sa.Index(
            "uq_ai_summary_job_inflight",
            "cache_key",
            unique=True,
            postgresql_where=sa.text("status IN ('queued','running')"),
        ),
        sa.Index(
            "ix_ai_summary_job_cache",
            "cache_key",
            sa.text("finished_at DESC"),
            postgresql_where=sa.text("status = 'done'"),
        ),
        sa.Index(
            "ix_ai_summary_job_queue",
            "created_at",
            postgresql_where=sa.text("status IN ('queued','running')"),
        ),
        sa.Index("ix_ai_summary_job_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kam_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE")
    )
    scope_key: Mapped[str] = mapped_column(sa.Text, nullable=False)  # DealScope.cache_key()
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mode: Mapped[str] = mapped_column(sa.Text, nullable=False)
    provider: Mapped[str] = mapped_column(sa.Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)  # «версия данных»
    cache_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    model: Mapped[str | None] = mapped_column(sa.Text)
    tokens_in: Mapped[int | None] = mapped_column(sa.Integer)
    tokens_out: Mapped[int | None] = mapped_column(sa.Integer)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


__all__ = [
    "AiSummaryJob",
    "JOB_KINDS",
    "JOB_MODES",
    "JOB_PROVIDERS",
    "JOB_STATUSES",
    "PENDING_STATUSES",
]
