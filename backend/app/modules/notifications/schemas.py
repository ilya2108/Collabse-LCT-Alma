"""Pydantic-схемы модуля нотификаций (api-contract.md §9)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChannelPatch(BaseModel):
    enabled: bool = True
    address: str | None = None


class EventPatch(BaseModel):
    enabled: bool = True
    only_mine: bool | None = None


class SettingsIn(BaseModel):
    """`PUT /notifications/settings` — тело совпадает с ответом GET (§9.1)."""

    channels: dict[str, ChannelPatch] = Field(default_factory=dict)
    events: dict[str, EventPatch] = Field(default_factory=dict)
    version: int | None = None

    def as_storage(self) -> dict[str, Any]:
        return {
            "channels": {
                code: patch.model_dump(exclude_none=True)
                for code, patch in self.channels.items()
            },
            "events": {
                code: patch.model_dump(exclude_none=True)
                for code, patch in self.events.items()
            },
            "version": self.version,
        }


class TestSendIn(BaseModel):
    channel: Literal["telegram", "email", "max"]


class MarkReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None
    all: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> MarkReadIn:
        if not self.all and not self.ids:
            raise ValueError("Укажите ids или all=true")
        return self


class RuleIn(BaseModel):
    """Создание правила `notification_rule` (data-model.md §9.4)."""

    event_type: str
    channel: Literal["telegram", "email", "max"]
    recipient_type: Literal[
        "responsible", "manager_of_responsible", "role", "user", "tg_username", "email"
    ]
    recipient_value: str | None = None
    workflow_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class RulePatch(BaseModel):
    event_type: str | None = None
    channel: Literal["telegram", "email", "max"] | None = None
    recipient_type: (
        Literal[
            "responsible", "manager_of_responsible", "role", "user", "tg_username", "email"
        ]
        | None
    ) = None
    recipient_value: str | None = None
    workflow_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    params: dict[str, Any] | None = None
    is_active: bool | None = None
    version: int | None = None


class BotBindIn(BaseModel):
    """`POST /internal/bot/bind` (§9.2)."""

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    chat_id: int
    tg_username: str | None = None
