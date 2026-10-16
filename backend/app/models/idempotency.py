"""Ключи идемпотентности мутирующих запросов (api-contract.md §1.6, data-model.md §9.6).

Таблица создаётся миграцией 0003 (raw DDL); модель повторяет её колонка в колонку,
включая ограничения, поэтому `create_all` в тестовой БД совпадает с миграцией.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pii import EncryptedStr
from app.models.base import Base, CreatedAtMixin, uuid_pk


class IdempotencyKey(CreatedAtMixin, Base):
    """Ключ идемпотентности мутирующего запроса (api-contract.md §1.6, data-model.md §9.6)."""

    __tablename__ = "idempotency_key"
    __table_args__ = (
        sa.UniqueConstraint("principal", "idem_key", name="uq_idempotency_key_principal_key"),
        sa.CheckConstraint("status IN ('processing', 'completed')", name="status"),
        sa.CheckConstraint(
            "status <> 'completed' OR response_status IS NOT NULL", name="completed_response"
        ),
        sa.CheckConstraint("char_length(idem_key) BETWEEN 1 AND 255", name="key_length"),
        sa.Index("ix_idempotency_key_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # 'sub:<keycloak sub>' (люди и сервисные клиенты) | 'internal' (X-Internal-Token)
    principal: Mapped[str] = mapped_column(sa.Text, nullable=False)
    idem_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    method: Mapped[str] = mapped_column(sa.Text, nullable=False)
    path: Mapped[str] = mapped_column(sa.Text, nullable=False)  # путь + '?query'
    request_hash: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'processing'"), default="processing"
    )
    lock_token: Mapped[uuid.UUID | None] = mapped_column(sa.dialects.postgresql.UUID(as_uuid=True))
    locked_until: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    response_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    response_headers: Mapped[list[Any] | None] = mapped_column(JSONB)  # [[name, value], ...]
    # Fernet-шифртекст (bytea): ответ мутации может содержать ПДн (data-model.md §8)
    response_body: Mapped[str | None] = mapped_column(EncryptedStr)
    response_body_omitted: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    first_request_id: Mapped[str | None] = mapped_column(sa.Text)
    replay_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
