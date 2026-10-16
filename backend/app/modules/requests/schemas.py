"""Pydantic-схемы заявок (api-contract.md §4). Ресурс API — Request, таблица — deal."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

Money = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str)]


class _ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


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

    @model_validator(mode="after")
    def _check_client(self) -> RequestCreate:
        if self.workflow_type == "b2b" and self.university_id is None:
            raise ValueError("Для B2B-заявки обязателен вуз (university_id)")
        if self.workflow_type == "b2c" and self.counterparty_id is None:
            raise ValueError("Для B2C-заявки обязателен клиент (counterparty_id)")
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
