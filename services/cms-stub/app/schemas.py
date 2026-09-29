"""Pydantic-модель единого конверта IntegrationEvent (api-contract §13.1).

Заглушка — толерантный получатель: неизвестные поля игнорируются
(forward compatibility по контракту), недостающие блоки payload трактуются
как null, состав пяти блоков кейсодержателя не форсируется на приёме.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Correlation(BaseModel):
    """Ключи связи между системами; получатель возвращает их в ответных событиях."""

    model_config = ConfigDict(extra="ignore")

    crm_request_id: str | None = None
    lms_enrollment_id: str | None = None
    cms_lead_id: str | None = None


class IntegrationEnvelope(BaseModel):
    """Единый конверт события интеграции CRM↔LMS/CMS."""

    model_config = ConfigDict(extra="ignore")

    event_id: UUID
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    source: str = Field(min_length=1)
    correlation: Correlation = Field(default_factory=Correlation)
    payload: dict[str, Any] = Field(default_factory=dict)

    def block(self, name: str) -> dict[str, Any] | None:
        """Блок payload (status/responsible/university/program/product/links) или None."""
        value = self.payload.get(name)
        return value if isinstance(value, dict) else None
