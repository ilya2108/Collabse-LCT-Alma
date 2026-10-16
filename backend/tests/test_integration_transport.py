"""Транспорт исходящих интеграций: классификация доставки и HMAC (api-contract.md §13.7).

HTTP-получатель подменён `httpx.MockTransport`; outbox-логика (ретраи/DLQ) проверяется
на объекте события без БД — сессия-заглушка только принимает уведомления о DLQ.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import orjson
import pytest

from app.core import outbox
from app.core.config import get_settings
from app.core.events import INTG_CRM_PROGRAM_PUBLISHED, INTG_CRM_REQUEST_HANDED_OVER
from app.core.integration_transport import (
    SUPPORTED_INTEGRATION_TRANSPORTS,
    UNKNOWN_EVENT_ERROR,
    DeliveryOutcome,
    HttpIntegrationTransport,
    IntegrationTransport,
    integration_target_url,
    sign_payload,
)
from app.models.service import IntegrationEvent
from app.modules.integrations.security import signature_valid


def _event(event_type: str = INTG_CRM_REQUEST_HANDED_OVER, system: str = "lms") -> IntegrationEvent:
    event_id = uuid.uuid4()
    return IntegrationEvent(
        id=uuid.uuid4(),
        event_id=event_id,
        direction="outbound",
        system=system,
        event_type=event_type,
        entity_type="deal",
        entity_id=uuid.uuid4(),
        payload={"event_id": str(event_id), "event_type": event_type, "data": {"x": 1}},
        status="pending",
        attempts=0,
    )


async def _deliver(handler: Any, event: IntegrationEvent | None = None) -> DeliveryOutcome:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await HttpIntegrationTransport(client).deliver(event or _event())


class TestHttpTransport:
    def test_only_http_supported(self) -> None:
        assert SUPPORTED_INTEGRATION_TRANSPORTS == ("http",)
        assert HttpIntegrationTransport.name == "http"

    async def test_2xx_delivered_with_body(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"lms_enrollment_id": "E-1"})

        event = _event()
        outcome = await _deliver(handler, event)
        assert outcome == DeliveryOutcome("delivered", response_body={"lms_enrollment_id": "E-1"})
        request = seen[0]
        assert str(request.url) == integration_target_url(event.event_type, event.system)
        assert request.headers["content-type"] == "application/json"
        assert request.content == orjson.dumps(event.payload)
        secret = get_settings().lms_webhook_secret
        assert signature_valid(secret, request.content, request.headers["X-Webhook-Signature"])

    async def test_cms_signed_with_cms_secret(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"cms_external_id": "C-1"})

        await _deliver(handler, _event(INTG_CRM_PROGRAM_PUBLISHED, "cms"))
        settings = get_settings()
        header = seen[0].headers["X-Webhook-Signature"]
        assert signature_valid(settings.cms_webhook_secret, seen[0].content, header)
        assert "/api/v1/catalog/upsert" in str(seen[0].url)

    async def test_2xx_without_json_object(self) -> None:
        outcome = await _deliver(lambda request: httpx.Response(204))
        assert outcome == DeliveryOutcome("delivered", response_body={})
        outcome = await _deliver(lambda request: httpx.Response(200, json=[1, 2]))
        assert outcome.status == "delivered" and outcome.response_body == {}

    async def test_4xx_dead(self) -> None:
        outcome = await _deliver(lambda request: httpx.Response(404, text="нет такой программы"))
        assert outcome.status == "dead"
        assert outcome.error == "HTTP 404: нет такой программы"

    async def test_5xx_retry(self) -> None:
        outcome = await _deliver(lambda request: httpx.Response(503, text="busy"))
        assert outcome.status == "retry"
        assert outcome.error == "HTTP 503: busy"

    async def test_network_error_retry(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        outcome = await _deliver(handler)
        assert outcome.status == "retry"
        assert outcome.error is not None and outcome.error.startswith("Сетевая ошибка:")

    async def test_unknown_event_type_dead_without_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("запроса быть не должно")

        outcome = await _deliver(handler, _event("crm.unknown.event"))
        assert outcome == DeliveryOutcome("dead", UNKNOWN_EVENT_ERROR)

    def test_sign_payload_reexported_from_outbox(self) -> None:
        assert outbox.sign_payload is sign_payload
        assert signature_valid("s", b"{}", outbox.sign_payload("s", b"{}"))


class _FakeTransport:
    name = "fake"

    def __init__(self, outcome: DeliveryOutcome) -> None:
        self.outcome = outcome
        self.delivered: list[IntegrationEvent] = []

    async def deliver(self, event: IntegrationEvent) -> DeliveryOutcome:
        self.delivered.append(event)
        return self.outcome


class TestOutboxUsesTransport:
    """deliver_integration_event переводит исход транспорта в состояние события."""

    @pytest.fixture(autouse=True)
    def _no_side_effects(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        stored: list[Any] = []
        notified: list[Any] = []

        async def store_ids(session: Any, event: Any, body: dict[str, Any]) -> None:
            stored.append(body)

        async def notify(session: Any, **kwargs: Any) -> int:
            notified.append(kwargs)
            return 0

        monkeypatch.setattr(outbox, "_store_external_ids", store_ids)
        monkeypatch.setattr(outbox, "emit_event_notifications", notify)
        self.stored, self.notified = stored, notified
        return stored

    def test_fake_transport_matches_protocol(self) -> None:
        transport: IntegrationTransport = _FakeTransport(DeliveryOutcome("delivered"))
        assert transport.name == "fake"

    async def test_delivered(self) -> None:
        event = _event()
        transport = _FakeTransport(DeliveryOutcome("delivered", response_body={"a": 1}))
        assert await outbox.deliver_integration_event(None, event, transport)  # type: ignore[arg-type]
        assert event.status == "delivered"
        assert event.processed_at is not None and event.last_error is None
        assert self.stored == [{"a": 1}]
        assert transport.delivered == [event]

    async def test_retry_schedules_next_attempt(self) -> None:
        event = _event()
        transport = _FakeTransport(DeliveryOutcome("retry", "HTTP 503: busy"))
        assert not await outbox.deliver_integration_event(None, event, transport)  # type: ignore[arg-type]
        assert event.status == "retrying"
        assert event.attempts == 1
        assert event.last_error == "HTTP 503: busy"
        assert event.next_retry_at is not None
        assert self.notified == []

    async def test_dead_goes_to_dlq(self) -> None:
        event = _event()
        transport = _FakeTransport(DeliveryOutcome("dead", "HTTP 400: bad"))
        assert not await outbox.deliver_integration_event(None, event, transport)  # type: ignore[arg-type]
        assert event.status == "dead"
        assert event.last_error == "HTTP 400: bad"
        assert len(self.notified) == 1
