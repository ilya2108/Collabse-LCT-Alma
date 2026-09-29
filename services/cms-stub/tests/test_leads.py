"""Демо-форма лида: формирование cms.lead.created, доставка, обратная связь CRM."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import verify_signature

LEAD_FORM = {
    "full_name": "Смирнова Ольга Викторовна",
    "email": "smirnova@example.com",
    "phone": "+7 916 000-00-00",
    "client_kind": "person",
    "comment": "Интересует рассрочка",
}


def test_lead_form_enqueues_contract_envelope(
    app, client, signed_post, publish_event
) -> None:
    program_id = publish_event()["payload"]["program"]["crm_program_id"]
    signed_post("/api/v1/catalog/upsert", publish_event())

    response = client.post(
        "/demo/leads", data={**LEAD_FORM, "program_id": program_id}, follow_redirects=False
    )
    assert response.status_code == 303

    outbound = [e for e in app.state.storage.journal() if e["direction"] == "out"]
    assert len(outbound) == 1
    envelope = json.loads(outbound[0]["envelope"])
    assert envelope["event_type"] == "cms.lead.created"
    assert envelope["source"] == "cms"
    assert envelope["correlation"]["cms_lead_id"].startswith("lead-")
    payload = envelope["payload"]
    assert payload["status"] == {"code": "new", "name": "Новая заявка с сайта"}
    assert payload["responsible"] is None
    assert payload["university"] is None
    assert payload["program"]["crm_program_id"] == program_id
    assert payload["links"]["db_keys"] == {"program_id": program_id}
    lead = payload["lead"]
    assert lead["client_kind"] == "person"
    assert lead["full_name"] == "Смирнова Ольга Викторовна"
    assert lead["email"] == "smirnova@example.com"
    assert lead["comment"] == "Интересует рассрочка"
    # Лид записан и связан с событием.
    leads = app.state.storage.list_leads()
    assert leads[0]["cms_lead_id"] == envelope["correlation"]["cms_lead_id"]
    assert leads[0]["event_id"] == envelope["event_id"]


def test_lead_without_program_allowed(app, client) -> None:
    response = client.post("/demo/leads", data=LEAD_FORM, follow_redirects=False)
    assert response.status_code == 303
    entry = next(e for e in app.state.storage.journal() if e["direction"] == "out")
    envelope = json.loads(entry["envelope"])
    assert envelope["payload"]["program"] is None
    assert envelope["payload"]["links"]["db_keys"] == {}


def test_lead_requires_name_and_email(app, client) -> None:
    response = client.post(
        "/demo/leads", data={"full_name": "", "email": ""}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "flash_kind=err" in response.headers["location"]
    assert app.state.storage.list_leads() == []


def test_unknown_program_rejected(app, client) -> None:
    response = client.post(
        "/demo/leads", data={**LEAD_FORM, "program_id": "pg-nope"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "flash_kind=err" in response.headers["location"]
    assert app.state.storage.list_leads() == []


@pytest.fixture()
def delivered_env(settings: Settings):
    """Приложение с MockTransport: CRM отвечает 201 и возвращает crm_request_id."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["signature"] = request.headers.get("X-Webhook-Signature")
        return httpx.Response(
            201,
            json={"accepted": True, "duplicate": False, "crm_request_id": "rq-uuid-new"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    application = create_app(settings=settings, http_client=http_client)
    with TestClient(application) as test_client:
        yield application, test_client, seen
    asyncio.run(http_client.aclose())


def test_delivery_feedback_sets_crm_request_id(delivered_env) -> None:
    application, client, seen = delivered_env
    response = client.post("/demo/leads", data=LEAD_FORM, follow_redirects=False)
    assert response.status_code == 303

    asyncio.run(application.state.relay.flush_once())

    # Исходящий вебхук подписан тем же секретом и доставлен.
    assert verify_signature("test-cms-secret", seen["body"], seen["signature"])
    outbound = [e for e in application.state.storage.journal() if e["direction"] == "out"]
    assert outbound[0]["status"] == "delivered"
    # Обратная связь: CRM вернула id созданной B2C-заявки, он сохранён в лиде.
    lead = application.state.storage.list_leads()[0]
    assert lead["crm_request_id"] == "rq-uuid-new"
    assert lead["delivery_status"] == "delivered"


def test_relay_retries_5xx_then_dlq(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    application = create_app(settings=settings, http_client=http_client)
    with TestClient(application) as client:
        client.post("/demo/leads", data=LEAD_FORM, follow_redirects=False)
        for _ in range(3):  # первая попытка + 2 ретрая по расписанию (0, 0)
            asyncio.run(application.state.relay.flush_once())
        outbound = next(
            e for e in application.state.storage.journal() if e["direction"] == "out"
        )
        assert outbound["status"] == "dead"
        assert outbound["attempts"] == 3
        lead = application.state.storage.list_leads()[0]
        assert lead["delivery_status"] == "dead"
        assert lead["crm_request_id"] is None
    asyncio.run(http_client.aclose())
