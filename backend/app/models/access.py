"""Роли и права CRM (data-model.md §3): каталог прав, роли, связь «роль — право».

Назначение ролей пользователю — массив `app_user.roles` (коды ролей), источник истины —
БД Альмы; Keycloak отвечает только за вход. Права определяет код
(`app.core.rbac_catalog`), `app_permission` — его копия для внешних ключей.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, VersionedMixin, uuid_pk


class AppPermission(Base):
    __tablename__ = "app_permission"

    code: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    group_code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    is_scoped: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )


class AppRole(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "app_role"

    id: Mapped[uuid.UUID] = uuid_pk()
    # неизменяем: хранится в app_user.roles[] и workflow_transition.allowed_roles
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    is_system: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    # admin: права и область не редактируются (защита от потери управления)
    is_locked: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    workflow_types: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{b2b,b2c}'"),
        default=lambda: ["b2b", "b2c"],
    )
    record_scope: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'all'"), default="all"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("app_user.id", ondelete="SET NULL")
    )


class AppRolePermission(Base):
    __tablename__ = "app_role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("app_role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        sa.ForeignKey("app_permission.code", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )
