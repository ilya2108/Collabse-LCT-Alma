"""Pydantic-схемы справочников: вузы, контакты, договоры, продукты, программы.

Тела — api-contract.md §3. Поле `version` — optimistic locking (§1.3): PATCH обязан
передать текущее значение, расхождение → 409 stale_version.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

# денежные суммы — строка decimal в JSON (api-contract.md §1.3)
Money = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str)]


class _ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- вузы -------------------------------------------------------------------------------


class UniversityBase(_ApiModel):
    name: str = Field(min_length=2, max_length=500)
    short_name: str | None = None
    inn: str | None = Field(default=None, max_length=12)
    kpp: str | None = Field(default=None, max_length=9)
    region: str | None = None
    city: str | None = None
    website: str | None = None
    kam_user_id: UUID | None = None
    notes: str | None = None
    is_active: bool = True


class UniversityCreate(UniversityBase):
    pass


class UniversityUpdate(_ApiModel):
    version: int
    name: str | None = Field(default=None, min_length=2, max_length=500)
    short_name: str | None = None
    inn: str | None = Field(default=None, max_length=12)
    kpp: str | None = Field(default=None, max_length=9)
    region: str | None = None
    city: str | None = None
    website: str | None = None
    kam_user_id: UUID | None = None
    notes: str | None = None
    is_active: bool | None = None


class UniversityAggregates(_ApiModel):
    contracts_count: int
    active_requests: int
    students_count: int


class UniversityOut(UniversityBase):
    id: UUID
    version: int = Field(validation_alias=AliasChoices("xmin", "version"))
    created_at: datetime
    updated_at: datetime
    aggregates: UniversityAggregates | None = None


# --- контактные лица вуза ------------------------------------------------------------------


class ContactBase(_ApiModel):
    full_name: str = Field(min_length=2, max_length=300)
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False
    notes: str | None = None


class ContactCreate(ContactBase):
    pass


class ContactUpdate(_ApiModel):
    version: int
    full_name: str | None = Field(default=None, min_length=2, max_length=300)
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool | None = None
    notes: str | None = None


class ContactOut(ContactBase):
    id: UUID
    university_id: UUID
    version: int = Field(validation_alias=AliasChoices("xmin", "version"))
    created_at: datetime


# --- договоры ------------------------------------------------------------------------------


ContractStatus = Literal["draft", "negotiation", "active", "completed", "terminated"]


class ContractBase(_ApiModel):
    number: str = Field(min_length=1, max_length=100)
    university_id: UUID | None = None
    counterparty_id: UUID | None = None
    status: ContractStatus = "draft"
    signed_at: date | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    amount: Money | None = None
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    responsible_user_id: UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_owner_and_dates(self) -> ContractBase:
        if (self.university_id is None) == (self.counterparty_id is None):
            raise ValueError(
                "У договора должен быть ровно один владелец: вуз или контрагент"
            )
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("Дата окончания договора не может быть раньше даты начала")
        return self


class ContractCreate(ContractBase):
    product_ids: list[UUID] = Field(default_factory=list)


class ContractUpdate(_ApiModel):
    version: int
    number: str | None = Field(default=None, min_length=1, max_length=100)
    status: ContractStatus | None = None
    signed_at: date | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    amount: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    responsible_user_id: UUID | None = None
    notes: str | None = None
    product_ids: list[UUID] | None = None


class FileOut(_ApiModel):
    id: UUID
    file_name: str = Field(validation_alias="original_name")
    content_type: str | None
    size_bytes: int | None
    s3_key: str
    uploaded_by: UUID | None
    created_at: datetime


class ContractOut(ContractBase):
    id: UUID
    product_ids: list[UUID] = Field(default_factory=list)
    version: int = Field(validation_alias=AliasChoices("xmin", "version"))
    created_at: datetime
    updated_at: datetime
    warning_duplicate: str | None = None
    files: list[FileOut] | None = None


# --- продукты ---------------------------------------------------------------------------------


ProductType = Literal["course", "dpo_program", "network_program", "service", "other"]


class ProductBase(_ApiModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    product_type: ProductType = "course"
    description: str | None = None
    is_active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(_ApiModel):
    version: int
    code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    product_type: ProductType | None = None
    description: str | None = None
    is_active: bool | None = None


class ProductOut(ProductBase):
    id: UUID
    version: int = Field(validation_alias=AliasChoices("xmin", "version"))
    created_at: datetime
    updated_at: datetime


# --- программы и приоритеты --------------------------------------------------------------------


class ProgramBase(_ApiModel):
    product_id: UUID | None = None
    university_id: UUID | None = None
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    duration_hours: int | None = Field(default=None, gt=0)
    seats: int | None = Field(default=None, gt=0)
    starts_on: date | None = None
    published_to_cms: bool = False
    priority_rank: int = Field(default=1000, ge=0)
    is_active: bool = True


class ProgramCreate(ProgramBase):
    pass


class ProgramUpdate(_ApiModel):
    version: int
    product_id: UUID | None = None
    university_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    duration_hours: int | None = Field(default=None, gt=0)
    seats: int | None = Field(default=None, gt=0)
    starts_on: date | None = None
    published_to_cms: bool | None = None
    is_active: bool | None = None


class ProgramOut(ProgramBase):
    id: UUID
    priority_updated_by: UUID | None = None
    priority_updated_at: datetime | None = None
    cms_external_id: str | None = None  # read-only, из external_link
    lms_course_id: str | None = None  # read-only, из external_link
    version: int = Field(validation_alias=AliasChoices("xmin", "version"))
    created_at: datetime
    updated_at: datetime


class PriorityPatch(_ApiModel):
    priority_rank: int = Field(ge=0)
    version: int


class ReorderItem(_ApiModel):
    id: UUID
    priority_rank: int = Field(ge=0)


class ReorderBody(_ApiModel):
    items: list[ReorderItem] = Field(min_length=1)
