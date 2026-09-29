"""`app_user` — зеркало пользователей Keycloak (data-model.md §3)."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, VersionedMixin, uuid_pk


class AppUser(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = uuid_pk()
    keycloak_sub: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    username: Mapped[str] = mapped_column(sa.Text, nullable=False)
    email: Mapped[str | None] = mapped_column(sa.Text)
    full_name: Mapped[str | None] = mapped_column(sa.Text)
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"), default=list
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )
    tg_chat_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    tg_username: Mapped[str | None] = mapped_column(sa.Text)
    tg_linked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )
