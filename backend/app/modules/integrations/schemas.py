"""Pydantic-схемы конверта IntegrationEvent для входящих вебхуков (api-contract.md §13.1).

`InboundEnvelope` разбирает тело вебхука; `payload` остаётся словарём (неизвестные блоки
не теряются, сервис читает нужные ключи сам). Модели блоков payload и ответов вебхуков
нужны только Swagger: они описывают то, что уже принимает и отдаёт код, и не участвуют
в разборе.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Correlation(BaseModel):
    """Ключи связи между системами; получатель возвращает их в ответных событиях."""

    model_config = ConfigDict(extra="ignore")

    crm_request_id: UUID | None = Field(
        default=None, description="Заявка CRM; обязателен для событий LMS"
    )
    lms_enrollment_id: str | None = Field(default=None, description="Зачисление в LMS")
    cms_lead_id: str | None = Field(default=None, description="Лид на сайте (CMS)")


class LeadBlock(BaseModel):
    """Расширение конверта только для cms.lead.created; ПДн шифруются при записи."""

    model_config = ConfigDict(extra="ignore")

    client_kind: str = Field(
        pattern="^(person|company)$",
        description="`person` — физлицо (нужны full_name и email), `company` — юрлицо "
        "(нужен company_name)",
    )
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company_name: str | None = None
    inn: str | None = None
    comment: str | None = Field(default=None, description="Становится описанием заявки")


# --- блоки payload: только описание для Swagger (разбор — словарём в service) -------------


class _PayloadBlock(BaseModel):
    model_config = ConfigDict(extra="allow")


class PayloadStatus(_PayloadBlock):
    code: str | None = None
    name: str | None = Field(default=None, description="Попадает в комментарий истории заявки")


class PayloadResponsible(_PayloadBlock):
    crm_user_id: UUID | None = None
    full_name: str | None = None
    email: str | None = None


class PayloadUniversity(_PayloadBlock):
    crm_university_id: UUID | None = None
    name: str | None = None
    inn: str | None = None


class PayloadProgram(_PayloadBlock):
    crm_program_id: UUID | None = Field(
        default=None, description="Для cms.lead.created — программа новой заявки"
    )
    name: str | None = None
    lms_course_id: str | None = None
    cms_external_id: str | None = None


class PayloadProduct(_PayloadBlock):
    crm_product_id: UUID | None = Field(
        default=None, description="Для cms.lead.created — продукт новой заявки"
    )
    code: str | None = None
    name: str | None = None


class PayloadLinks(_PayloadBlock):
    db_keys: dict[str, str] = Field(default_factory=dict, description="Ключи связи с БД")
    s3_keys: list[str] = Field(default_factory=list, description="Ключи объектов в S3")


class IntegrationPayload(_PayloadBlock):
    """Пять блоков кейсодержателя (неприменимые — null) + `links`; `lead` — только у
    cms.lead.created. Неизвестные блоки получатель игнорирует."""

    status: PayloadStatus | None = None
    responsible: PayloadResponsible | None = None
    university: PayloadUniversity | None = None
    program: PayloadProgram | None = None
    product: PayloadProduct | None = None
    links: PayloadLinks | None = None
    lead: LeadBlock | None = Field(default=None, description="Обязателен для cms.lead.created")


class _DocumentedAs:
    """Метаданные Annotated: в JSON Schema поле описывается моделью `model`, а разбор
    остаётся прежним (тип поля не меняется)."""

    def __init__(self, model: type[BaseModel]) -> None:
        self.model = model

    def __get_pydantic_json_schema__(self, core_schema: Any, handler: Any) -> Any:
        return handler(self.model.__pydantic_core_schema__)


class InboundEnvelope(BaseModel):
    """Конверт входящего события. Неизвестные поля игнорируются (forward compatibility,
    правило конверта §13.1)."""

    model_config = ConfigDict(extra="ignore")

    event_id: UUID = Field(description="Ключ идемпотентности приёма: повтор → duplicate")
    event_type: str = Field(
        description="LMS: `lms.learning.started`, `lms.learning.progress`, "
        "`lms.learning.completed`; CMS: `cms.lead.created`",
        examples=["lms.learning.completed"],
    )
    occurred_at: datetime = Field(
        description="Не старше 10 минут, иначе 409 (кроме повтора уже принятого event_id)"
    )
    source: str = Field(examples=["lms"])
    correlation: Correlation = Field(default_factory=Correlation)
    payload: Annotated[dict[str, Any], _DocumentedAs(IntegrationPayload)] = Field(
        default_factory=dict
    )


# --- ответы вебхуков (api-contract.md §13.3, §13.4) ----------------------------------------


class LmsWebhookAck(BaseModel):
    accepted: bool = True
    duplicate: bool = Field(description="true — event_id уже принимали, эффектов нет")
    received_event_id: UUID


class CmsWebhookAck(BaseModel):
    accepted: bool = True
    duplicate: bool = Field(description="true — event_id уже принимали, эффектов нет")
    crm_request_id: UUID | None = Field(description="Созданная (или ранее созданная) заявка")
