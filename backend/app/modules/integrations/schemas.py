"""Pydantic-схемы конверта IntegrationEvent для входящих вебхуков (api-contract.md §13.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Correlation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    crm_request_id: UUID | None = None
    lms_enrollment_id: str | None = None
    cms_lead_id: str | None = None


class LeadBlock(BaseModel):
    """Расширение конверта только для cms.lead.created; ПДн шифруются при записи."""

    model_config = ConfigDict(extra="ignore")

    client_kind: str = Field(pattern="^(person|company)$")
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company_name: str | None = None
    inn: str | None = None
    comment: str | None = None


class InboundEnvelope(BaseModel):
    """Неизвестные поля игнорируются (forward compatibility, правило конверта §13.1)."""

    model_config = ConfigDict(extra="ignore")

    event_id: UUID
    event_type: str
    occurred_at: datetime
    source: str
    correlation: Correlation = Field(default_factory=Correlation)
    payload: dict[str, Any] = Field(default_factory=dict)
