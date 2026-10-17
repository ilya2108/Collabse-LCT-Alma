"""Pydantic-схемы заявок (api-contract.md §4). Ресурс API — Request, таблица — deal."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

Money = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str)]


class _ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# те же правила, что у формы создания заявки (CreateRequestDrawer: EMAIL_RE, INN_RE)
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_INN_RE = re.compile(r"\d{10}|\d{12}")


class NewClientIn(_ApiModel):
    """Новый клиент B2C из формы создания заявки — вместо `counterparty_id`.

    Физлицо — `full_name` (email и телефон по желанию), юрлицо — `company_name` (ИНН,
    email, телефон по желанию). Пустые строки — «не заполнено». Сервер ищет клиента в базе
    (физлицо — по email, юрлицо — по ИНН, без ИНН — по названию) и создаёт нового, если не
    нашёл; ПДн физлица шифруются при записи (Fernet + blind index).
    """

    full_name: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=50)
    company_name: str | None = Field(default=None, max_length=500)
    inn: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.fullmatch(value):
            raise PydanticCustomError("invalid_format", "Некорректный email")
        return value

    @field_validator("inn")
    @classmethod
    def _check_inn(cls, value: str | None) -> str | None:
        if value is not None and not _INN_RE.fullmatch(value):
            raise PydanticCustomError("invalid_format", "ИНН — 10 или 12 цифр")
        return value


class RequestCreate(_ApiModel):
    workflow_type: Literal["b2b", "b2c"]
    title: str = Field(min_length=2, max_length=500)
    university_id: UUID | None = None
    counterparty_id: UUID | None = None
    interaction_type_id: UUID | None = None
    contract_id: UUID | None = None
    product_id: UUID | None = None
    program_id: UUID | None = None
    assignee_id: UUID | None = None  # по умолчанию — создатель
    amount: Money | None = None
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    description: str | None = None
    # B2C: существующий клиент — `counterparty_id`, новый из формы — `client_kind` + `client`
    # (без client_kind тип выводится по полям: есть company_name — юрлицо, иначе физлицо)
    client_kind: Literal["person", "company"] | None = None
    client: NewClientIn | None = None

    @model_validator(mode="after")
    def _check_client(self) -> RequestCreate:
        if self.workflow_type == "b2b":
            if self.university_id is None:
                raise PydanticCustomError(
                    "missing", "Для B2B-заявки обязателен вуз (university_id)"
                )
            if self.client is not None:
                raise PydanticCustomError(
                    "not_allowed", "Клиент (client) указывается только для B2C-заявки"
                )
            return self
        if self.counterparty_id is None and self.client is None:
            raise PydanticCustomError(
                "missing", "Для B2C-заявки обязателен клиент (counterparty_id или client)"
            )
        if self.counterparty_id is not None and self.client is not None:
            raise PydanticCustomError(
                "conflict",
                "Укажите либо существующего клиента (counterparty_id), "
                "либо нового (client), но не оба сразу",
            )
        if self.client is not None:
            if self.client_kind is None:
                self.client_kind = "company" if self.client.company_name else "person"
            if self.client_kind == "person" and self.client.full_name is None:
                raise PydanticCustomError("missing", "Укажите ФИО клиента (client.full_name)")
            if self.client_kind == "company" and self.client.company_name is None:
                raise PydanticCustomError(
                    "missing", "Укажите название организации (client.company_name)"
                )
        return self


class RequestUpdate(_ApiModel):
    """PATCH полей заявки; статус меняется только через /transitions (I-4)."""

    version: int
    title: str | None = Field(default=None, min_length=2, max_length=500)
    university_id: UUID | None = None
    counterparty_id: UUID | None = None
    interaction_type_id: UUID | None = None
    contract_id: UUID | None = None
    product_id: UUID | None = None
    program_id: UUID | None = None
    amount: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = None


class AssignBody(_ApiModel):
    assignee_id: UUID
    version: int | None = None  # optimistic lock опционален (контракт §4.1 тела не требует)


# файлов на одном переходе — не больше (защита от случайной «папки целиком»)
MAX_TRANSITION_FILES = 10


class TransitionBody(_ApiModel):
    to_status_id: UUID
    comment: str | None = Field(default=None, max_length=4000)
    version: int
    # файлы, загруженные этим пользователем в заявку перед переходом (POST /files):
    # после перехода они привязываются к его строке истории — «файл в статусе» (ТЗ, ФТ3)
    file_ids: list[UUID] = Field(default_factory=list, max_length=MAX_TRANSITION_FILES)


class ReopenBody(_ApiModel):
    to_status_id: UUID
    comment: str = Field(min_length=1, max_length=4000)


class CommentCreate(_ApiModel):
    text: str = Field(min_length=1, max_length=8000)


class StatusRef(BaseModel):
    id: UUID
    code: str
    name: str
    color: str | None = None


class UserRef(BaseModel):
    id: UUID
    full_name: str | None = None


class ClientOut(BaseModel):
    """Сериализация контрагента B2C; ПДн физлица маскируются для observer (§4.2)."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company_name: str | None = None
    inn: str | None = None


class ExternalRefs(BaseModel):
    cms_lead_id: str | None = None
    lms_enrollment_id: str | None = None


class RequestOut(BaseModel):
    id: UUID
    workflow_type: str
    workflow_id: UUID
    title: str
    university_id: UUID | None = None
    client_kind: Literal["person", "company"] | None = None
    counterparty_id: UUID | None = None
    interaction_type_id: UUID | None = None
    client: ClientOut | None = None
    contract_id: UUID | None = None
    product_id: UUID | None = None
    program_id: UUID | None = None
    status: StatusRef
    request_status: str = Field(description="open | won | lost")  # доменный статус deal.status
    assignee: UserRef | None = None
    source: str
    external_refs: ExternalRefs
    amount: str | None = None
    currency: str = "RUB"
    status_updated_at: datetime
    last_activity_at: datetime
    is_stuck: bool = False
    stuck_days: int = 0
    description: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    available_transitions: list[dict[str, Any]] | None = None
    last_comments: list[dict[str, Any]] | None = None


class RequestFileOut(BaseModel):
    """Файл заявки: метаданные + этап, в котором его приложили (`stage_history_id`)."""

    id: UUID
    file_name: str
    content_type: str | None = None
    size_bytes: int | None = None
    s3_key: str
    uploaded_by: UUID | None = None
    uploaded_by_name: str | None = None
    created_at: datetime
    stage_history_id: UUID | None = None
    # снапшот имени этапа строки истории — как этап назывался, когда файл приложили
    stage_name: str | None = None


class PortfolioTransferBody(_ApiModel):
    """Передача портфеля КАМа (уход, отпуск, перераспределение) одним действием."""

    from_user_id: UUID
    to_user_id: UUID
    include_universities: bool = True
    # предпросмотр: тот же отчёт без изменений — для подтверждения в интерфейсе
    dry_run: bool = False

    @model_validator(mode="after")
    def _check_users(self) -> PortfolioTransferBody:
        if self.from_user_id == self.to_user_id:
            raise ValueError("Передать портфель можно только другому сотруднику")
        return self


class CommentOut(BaseModel):
    id: UUID
    text: str
    author: UserRef
    created_at: datetime
    deleted: bool = False
