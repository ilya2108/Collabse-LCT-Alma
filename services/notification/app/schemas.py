"""Pydantic-схемы HTTP API notification-service."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

Channel = Literal["telegram", "email", "max"]


class SendRequest(BaseModel):
    """Тело POST /api/v1/send — одна запись outbox-таблицы `notification` backend'а.

    Канонические имена — notification_id/channel/recipient/subject/text/meta;
    алиасы address/body/payload соответствуют колонкам data-model.md §9.4,
    чтобы relay мог слать строку outbox как есть.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    notification_id: str = Field(min_length=1, max_length=200)
    channel: Channel
    recipient: str = Field(
        min_length=1, max_length=500, validation_alias=AliasChoices("recipient", "address")
    )
    subject: str | None = Field(default=None, max_length=500)
    text: str | None = Field(default=None, validation_alias=AliasChoices("text", "body"))
    meta: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("meta", "payload")
    )
    event: str | None = Field(
        default=None, max_length=100, validation_alias=AliasChoices("event", "event_type")
    )
    template: str | None = Field(default=None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def _flatten_envelope(cls, data: Any) -> Any:
        """Конверт api-contract.md §9.4 → плоская форма.

        Relay backend'а шлёт {notification_id, event, recipients: [...], payload,
        template}; канал и адрес лежат в первом элементе recipients (backend
        вычисляет получателей заранее — по строке outbox на получателя/канал).
        """
        if not isinstance(data, dict) or not data.get("recipients"):
            return data
        if data.get("channel") and (data.get("recipient") or data.get("address")):
            return data  # плоская форма уже заполнена
        data = dict(data)
        first = data["recipients"][0] or {}
        channels = [c for c in (first.get("channels") or []) if c != "in_app"]
        channel = data.get("channel") or (channels[0] if channels else None)
        recipient = data.get("recipient") or data.get("address")
        if recipient is None:
            if channel == "telegram":
                chat_id = first.get("telegram_chat_id")
                recipient = str(chat_id) if chat_id is not None else first.get("address")
            else:
                recipient = first.get("email") or first.get("address")
        payload = data.get("payload") or data.get("meta") or {}
        data.setdefault("subject", payload.get("subject"))
        data.setdefault("text", payload.get("body"))
        if channel is not None:
            data["channel"] = channel
        if recipient is not None:
            data["recipient"] = str(recipient)
        return data

    @model_validator(mode="after")
    def _text_or_template(self) -> SendRequest:
        if not self.text and not self.template:
            raise ValueError("Нужно передать text (готовый текст) или template (имя шаблона)")
        return self


class SendResponse(BaseModel):
    accepted: bool = True


class OutboxResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int


class BotStats(BaseModel):
    enabled: bool
    username: str | None = None
    deep_link_template: str | None = None
    commands: dict[str, int]
    commands_total: int
    rate_limited: int
    bindings_created: int
    bindings_removed: int


class StatsResponse(BaseModel):
    """GET /api/v1/stats — операционные счётчики с момента старта процесса."""

    service: str = "notification"
    started_at: str
    uptime_seconds: int
    channels: dict[str, str]  # канал -> режим ('active' | 'stub')
    sent: dict[str, int]
    failed: dict[str, int]
    duplicates_suppressed: int
    telegram_retries: int
    bot: BotStats


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, str]
