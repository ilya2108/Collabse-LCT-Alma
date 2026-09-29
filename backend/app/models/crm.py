"""Организации, клиенты, каталог, договоры (data-model.md §4)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.pii import EncryptedStr
from app.models.base import Base, CreatedAtMixin, TimestampMixin, VersionedMixin, uuid_pk


class University(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "university"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(sa.Text)
    inn: Mapped[str | None] = mapped_column(sa.Text)  # у филиалов может совпадать — не unique
    kpp: Mapped[str | None] = mapped_column(sa.Text)
    region: Mapped[str | None] = mapped_column(sa.Text)
    city: Mapped[str | None] = mapped_column(sa.Text)
    website: Mapped[str | None] = mapped_column(sa.Text)
    kam_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )

    contacts: Mapped[list[UniversityContact]] = relationship(
        back_populates="university", cascade="all, delete-orphan", passive_deletes=True
    )


class UniversityContact(CreatedAtMixin, VersionedMixin, Base):
    __tablename__ = "university_contact"

    id: Mapped[uuid.UUID] = uuid_pk()
    university_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("university.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    position: Mapped[str | None] = mapped_column(sa.Text)
    email: Mapped[str | None] = mapped_column(sa.Text)
    phone: Mapped[str | None] = mapped_column(sa.Text)
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)

    university: Mapped[University] = relationship(back_populates="contacts")


class Counterparty(TimestampMixin, VersionedMixin, Base):
    """Клиент B2C: физлицо или юрлицо (дискриминатор `kind`).

    Атрибуты `full_name`/`email`/`phone` в Python — открытые строки, в БД — bytea-шифртекст
    (колонки `*_enc`, тип EncryptedStr). HMAC-колонки заполняет доменный сервис.
    """

    __tablename__ = "counterparty"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)  # person | legal_entity
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column("full_name_enc", EncryptedStr)
    email: Mapped[str | None] = mapped_column("email_enc", EncryptedStr)
    phone: Mapped[str | None] = mapped_column("phone_enc", EncryptedStr)
    email_hmac: Mapped[str | None] = mapped_column(sa.CHAR(64))
    full_name_hmac: Mapped[str | None] = mapped_column(sa.CHAR(64))
    org_name: Mapped[str | None] = mapped_column(sa.Text)
    inn: Mapped[str | None] = mapped_column(sa.Text)
    kpp: Mapped[str | None] = mapped_column(sa.Text)
    org_email: Mapped[str | None] = mapped_column(sa.Text)
    org_phone: Mapped[str | None] = mapped_column(sa.Text)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )


class InteractionType(CreatedAtMixin, VersionedMixin, Base):
    """Типы взаимодействия B2C — редактируемый справочник, не enum (data-model.md §4.4)."""

    __tablename__ = "interaction_type"

    id: Mapped[uuid.UUID] = uuid_pk()
    pipeline: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'b2c'"), default="b2c"
    )
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    sort_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("100"), default=100
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))


class Product(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "product"

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    product_type: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'course'"), default="course"
    )
    description: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )


class Program(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "program"

    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("product.id", ondelete="RESTRICT")
    )
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("university.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    duration_hours: Mapped[int | None] = mapped_column(sa.Integer)
    seats: Mapped[int | None] = mapped_column(sa.Integer)
    starts_on: Mapped[date | None] = mapped_column(sa.Date)
    published_to_cms: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false(), default=False
    )
    # ручное ранжирование, меньше = выше (Р-12)
    priority_rank: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1000"), default=1000
    )
    priority_updated_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    priority_updated_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true(), default=True
    )


class Contract(TimestampMixin, VersionedMixin, Base):
    __tablename__ = "contract"

    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[str] = mapped_column(sa.Text, nullable=False)
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("university.id", ondelete="RESTRICT")
    )
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("counterparty.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'draft'"), default="draft"
    )
    signed_at: Mapped[date | None] = mapped_column(sa.Date)
    valid_from: Mapped[date | None] = mapped_column(sa.Date)
    valid_to: Mapped[date | None] = mapped_column(sa.Date)
    amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    currency: Mapped[str] = mapped_column(
        sa.CHAR(3), nullable=False, server_default=sa.text("'RUB'"), default="RUB"
    )
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    notes: Mapped[str | None] = mapped_column(sa.Text)

    products: Mapped[list[ContractProduct]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", passive_deletes=True
    )


class ContractProduct(Base):
    """M:N «1 договор — N продуктов»; added_by/added_at — история расширения набора."""

    __tablename__ = "contract_product"

    contract_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("contract.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("product.id", ondelete="RESTRICT"), primary_key=True
    )
    note: Mapped[str | None] = mapped_column(sa.Text)
    added_by: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("app_user.id"))
    added_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    contract: Mapped[Contract] = relationship(back_populates="products")
    product: Mapped[Product] = relationship()


__all__ = [
    "University",
    "UniversityContact",
    "Counterparty",
    "InteractionType",
    "Product",
    "Program",
    "Contract",
    "ContractProduct",
]
