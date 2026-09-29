"""Declarative base, naming convention и общие миксины моделей.

Схема БД — docs/design/data-model.md (источник правды по DDL). Начальная миграция
написана вручную (raw DDL из документа); модели повторяют её колонка в колонку.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import FetchedValue, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# data-model.md §1.6
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: PG_UUID(as_uuid=True),
        dict[str, Any]: sa.dialects.postgresql.JSONB,
    }


def uuid_pk() -> Mapped[uuid.UUID]:
    """PK uuid: server_default gen_random_uuid() + клиентский default для ORM-инсертов."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class VersionedMixin:
    """Optimistic locking через системную колонку PostgreSQL `xmin` (без изменения DDL).

    api-contract.md §1.3 требует у мутабельных сущностей поле `version` (409 stale_version),
    а DDL data-model.md собственной колонки версии не содержит. Используем документированный
    приём SQLAlchemy «server side version counters»: `xmin` меняется при каждом UPDATE,
    маппер добавляет её в WHERE и поднимает StaleDataError при конкуренции. В API `version`
    сериализуется из `xmin`.
    """

    @declared_attr
    def xmin(cls) -> Mapped[int]:  # noqa: N805 — SQLAlchemy declared_attr
        return mapped_column(
            "xmin",
            sa.Integer,
            system=True,
            nullable=False,
            server_default=FetchedValue(),
        )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        return {
            "version_id_col": cls.xmin,
            "version_id_generator": False,
            "eager_defaults": True,
        }
