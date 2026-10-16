"""`app_user` — зеркало пользователей Keycloak (data-model.md §3).

Роли (`roles` — коды `app_role`) назначаются в Альме: источник истины — БД, Keycloak
отвечает только за вход. Эффективные права — свойство `access` (app.core.access).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, VersionedMixin, uuid_pk

if TYPE_CHECKING:
    from app.core.access import EffectiveAccess


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

    @property
    def access(self) -> EffectiveAccess:
        """Эффективные права по снимку каталога ролей (core/access.py); без загруженного
        каталога — системные роли из кода."""
        from app.core.access import access_for_roles

        return access_for_roles(self.roles or [])
