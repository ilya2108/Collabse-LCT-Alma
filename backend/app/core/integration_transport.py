"""Транспорт исходящих интеграций (api-contract.md §13.7).

Источник истины — outbox `integration_event` в PostgreSQL: событие пишется в одной
транзакции с бизнес-операцией, ретраи (1м → 5м → 30м → 2ч → 6ч) и DLQ ведёт
app.core.outbox. Транспорт только доставляет одно событие и классифицирует исход:
доставлено / повторить позже / в DLQ.

Единственная реализация — `HttpIntegrationTransport` (HTTP POST + HMAC-подпись
`X-Webhook-Signature`). Режим `stream` (KeyDB Streams) убран осознанно: KeyDB у нас
эфемерный (`emptyDir`) — шина на нём теряет события при рестарте, а вторая очередь
рядом с outbox — классический dual-write. Корпоративная шина заказчика (Kafka/ESB)
подключается новой реализацией `IntegrationTransport` без смены конверта, outbox,
ретраев и DLQ; `event_id` конверта — ключ сообщения.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
import orjson

from app.core.config import get_settings
from app.core.events import (
    INTG_CRM_PROGRAM_PUBLISHED,
    INTG_CRM_PROGRAM_UNPUBLISHED,
    INTG_CRM_PROGRAM_UPSERTED,
    INTG_CRM_REQUEST_HANDED_OVER,
)
from app.models.service import IntegrationEvent

logger = logging.getLogger("app.outbox")

# допустимые значения настройки integration_mode (admin: GET/PUT /admin/settings)
SUPPORTED_INTEGRATION_TRANSPORTS: tuple[str, ...] = ("http",)

UNKNOWN_EVENT_ERROR = "Неизвестный тип события (нет адресата)"


@dataclass(frozen=True)
class DeliveryOutcome:
    """Исход одной попытки доставки.

    `delivered` — получатель принял событие (`response_body` — его ответ, если это
    JSON-объект); `retry` — временный сбой (сеть, 5xx), outbox повторит по расписанию;
    `dead` — ошибка контракта (4xx, нет адресата), событие уходит в DLQ.
    """

    status: Literal["delivered", "retry", "dead"]
    error: str | None = None
    response_body: dict[str, Any] | None = None


class IntegrationTransport(Protocol):
    """Точка расширения: доставка одного outbound-события внешней системе."""

    name: str

    async def deliver(self, event: IntegrationEvent) -> DeliveryOutcome: ...


def sign_payload(secret: str, raw_body: bytes) -> str:
    """`X-Webhook-Signature: sha256=<hex HMAC-SHA256(secret, raw_body)>` (§13.6)."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def integration_target_url(event_type: str, system: str) -> str | None:
    """Адрес получателя по типу события; None — тип неизвестен (событие уйдёт в DLQ)."""
    settings = get_settings()
    lms = settings.lms_base_url.rstrip("/")
    cms = settings.cms_base_url.rstrip("/")
    mapping = {
        INTG_CRM_REQUEST_HANDED_OVER: f"{lms}/api/v1/enrollments",
        INTG_CRM_PROGRAM_UPSERTED: f"{lms}/api/v1/programs/upsert",
        INTG_CRM_PROGRAM_PUBLISHED: f"{cms}/api/v1/catalog/upsert",
        INTG_CRM_PROGRAM_UNPUBLISHED: f"{cms}/api/v1/catalog/unpublish",
    }
    url = mapping.get(event_type)
    if url is None:
        logger.error("Неизвестный тип outbound-события %s (system=%s)", event_type, system)
    return url


class HttpIntegrationTransport:
    """HTTP POST конверта с HMAC-подписью; секрет — по системе-получателю."""

    name = "http"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def deliver(self, event: IntegrationEvent) -> DeliveryOutcome:
        url = integration_target_url(event.event_type, event.system)
        if url is None:
            return DeliveryOutcome("dead", UNKNOWN_EVENT_ERROR)
        settings = get_settings()
        secret = (
            settings.lms_webhook_secret if event.system == "lms" else settings.cms_webhook_secret
        )
        raw_body = orjson.dumps(event.payload)
        try:
            response = await self._client.post(
                url,
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sign_payload(secret, raw_body),
                },
            )
        except httpx.HTTPError as exc:
            return DeliveryOutcome("retry", f"Сетевая ошибка: {exc}")

        if response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                body = {}
            return DeliveryOutcome(
                "delivered", response_body=body if isinstance(body, dict) else {}
            )
        error = f"HTTP {response.status_code}: {response.text[:500]}"
        if 400 <= response.status_code < 500:
            # ошибка контракта — не ретраим (§13.7), чинится руками через админку
            return DeliveryOutcome("dead", error)
        return DeliveryOutcome("retry", error)
