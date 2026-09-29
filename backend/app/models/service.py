"""Сервисные сущности: импорт, файлы, интеграции, нотификации, настройки, аудит.

data-model.md §9.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk


class FileObject(CreatedAtMixin, Base):
    """Метаданные объекта MinIO; `s3_key` — ключ связи для JSON-контракта интеграций."""

    __tablename__ = "file_object"

    id: Mapped[uuid.UUID] = uuid_pk()
    s3_bucket: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'crm-files'"), default="crm-files"
    )
    s3_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    original_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(sa.Text)
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    sha256: Mapped[str | None] = mapped_column(sa.CHAR(64))
    entity_type: Mapped[str | None] = mapped_column(sa.Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.dialects.postgresql.UUID(as_uuid=True))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


class ImportSession(TimestampMixin, VersionedMixin, Base):
    """Диалоговый импорт с интерактивным маппингом; машина состояний — data-model.md §9.1."""

    __tablename__ = "import_session"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("file_object.id"))
    original_filename: Mapped[str | None] = mapped_column(sa.Text)
    file_format: Mapped[str | None] = mapped_column(sa.Text)  # xlsx | xls | csv | json
    detected_encoding: Mapped[str | None] = mapped_column(sa.Text)
    detected_columns: Mapped[list[Any] | None] = mapped_column(JSONB)
    sample_rows: Mapped[list[Any] | None] = mapped_column(JSONB)  # ПДн маскируются
    column_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'"), default=dict
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'created'"), default="created"
    )
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("app_user.id"), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    row_errors: Mapped[list[ImportRowError]] = relationship(
        back_populates="import_session", cascade="all, delete-orphan", passive_deletes=True
    )


class ImportRowError(CreatedAtMixin, Base):
    __tablename__ = "import_row_error"

    id: Mapped[uuid.UUID] = uuid_pk()
    import_session_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("import_session.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # ПДн-колонки маскированы
    error: Mapped[str] = mapped_column(sa.Text, nullable=False)

    import_session: Mapped[ImportSession] = relationship(back_populates="row_errors")


class IntegrationEvent(CreatedAtMixin, Base):
    """Outbox/inbox двусторонних интеграций LMS/CMS — живой журнал обмена."""

    __tablename__ = "integration_event"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    direction: Mapped[str] = mapped_column(sa.Text, nullable=False)  # outbound | inbound
    system: Mapped[str] = mapped_column(sa.Text, nullable=False)  # lms | cms
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(sa.Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.dialects.postgresql.UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'"), default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    processed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


class ExternalLink(CreatedAtMixin, Base):
    """Связка id CRM-сущности с внешним id LMS/CMS."""

    __tablename__ = "external_link"

    entity_type: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True
    )
    system: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # lms | cms
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)


class NotificationRule(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "notification_rule"

    id: Mapped[uuid.UUID] = uuid_pk()
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow.id", ondelete="CASCADE")
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workflow_stage.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(sa.Text, nullable=False)  # telegram | email | max
    recipient_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    recipient_value: Mapped[str | None] = mapped_column(sa.Text)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'"), default=dict
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))


class Notification(CreatedAtMixin, Base):
    """Outbox нотификаций: пишется в транзакции бизнес-изменения, доставляет relay."""

    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = uuid_pk()
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("notification_rule.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(sa.Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.dialects.postgresql.UUID(as_uuid=True))
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    channel: Mapped[str] = mapped_column(sa.Text, nullable=False)
    address: Mapped[str] = mapped_column(sa.Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(sa.Text)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dedup_key: Mapped[str | None] = mapped_column(sa.Text)  # 'stuck:{deal}:{stage}:{bucket}'
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'"), default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    sent_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class AuditLog(Base):
    """Append-only журнал; ПДн в before/after маскируются до записи (data-model.md §8.5)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.Identity(always=True), primary_key=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id")
    )  # NULL = система
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.dialects.postgresql.UUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )


__all__ = [
    "FileObject",
    "ImportSession",
    "ImportRowError",
    "IntegrationEvent",
    "ExternalLink",
    "NotificationRule",
    "Notification",
    "AppSetting",
    "AuditLog",
]
