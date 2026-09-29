"""Исходящие вебхуки в CRM: формирование конверта, подпись, ретраи, DLQ."""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from app.config import Settings
from app.db import Storage
from app.outbox import WebhookRelay
from app.security import verify_signature


def _relay_env(tmp_path, handler, schedule=(0, 0)) -> tuple[Storage, WebhookRelay]:
    settings = Settings(
        webhook_secret="relay-secret",
        crm_webhook_url="http://crm.test/api/v1/integrations/lms/webhook",
        db_path=str(tmp_path / "relay.sqlite3"),
        retry_schedule=schedule,
        relay_auto_start=False,
    )
    storage = Storage(settings.db_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return storage, WebhookRelay(storage=storage, settings=settings, client=client)


def _enqueue_started(relay: WebhookRelay) -> str:
    return relay.enqueue(
        event_type="lms.learning.started",
        correlation={
            "crm_request_id": "rq-0001",
            "lms_enrollment_id": "lms-enr-7001",
            "cms_lead_id": None,
        },
        payload={
            "status": {"code": "in_learning", "name": "Обучение началось"},
            "responsible": None,
            "university": {"crm_university_id": "un-0001", "name": "МФТИ"},
            "program": None,
            "product": None,
            "links": {
                "db_keys": {"request_id": "rq-0001", "lms_enrollment_id": "lms-enr-7001"},
                "s3_keys": [],
            },
        },
        local_ref="lms-enr-7001",
    )


def test_relay_delivers_signed_envelope(tmp_path) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["signature"] = request.headers.get("X-Webhook-Signature")
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"accepted": True, "duplicate": False, "received_event_id": "x"}
        )

    storage, relay = _relay_env(tmp_path, handler)
    event_id = _enqueue_started(relay)
    asyncio.run(relay.flush_once())

    row = storage.get_outbound(event_id)
    assert row["status"] == "delivered"
    assert row["attempts"] == 1
    assert seen["url"] == "http://crm.test/api/v1/integrations/lms/webhook"
    # Подпись валидна ровно для отправленных байтов.
    assert verify_signature("relay-secret", seen["body"], seen["signature"])
    envelope = json.loads(seen["body"])
    assert envelope["event_type"] == "lms.learning.started"
    assert envelope["source"] == "lms"
    assert envelope["correlation"]["lms_enrollment_id"] == "lms-enr-7001"


def test_relay_retries_5xx_until_dead(tmp_path) -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="сервис недоступен")

    storage, relay = _relay_env(tmp_path, handler, schedule=(0, 0))
    event_id = _enqueue_started(relay)

    asyncio.run(relay.flush_once())
    assert storage.get_outbound(event_id)["status"] == "retrying"
    asyncio.run(relay.flush_once())
    assert storage.get_outbound(event_id)["status"] == "retrying"
    asyncio.run(relay.flush_once())

    row = storage.get_outbound(event_id)
    assert row["status"] == "dead"
    assert row["attempts"] == 3  # первая попытка + 2 ретрая по расписанию
    assert calls["n"] == 3
    assert "503" in row["last_error"]


def test_relay_4xx_goes_to_dlq_without_retries(tmp_path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    storage, relay = _relay_env(tmp_path, handler)
    event_id = _enqueue_started(relay)
    asyncio.run(relay.flush_once())

    row = storage.get_outbound(event_id)
    assert row["status"] == "dead"
    assert row["attempts"] == 1


def test_relay_retries_network_errors(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    storage, relay = _relay_env(tmp_path, handler, schedule=(3600,))
    event_id = _enqueue_started(relay)
    asyncio.run(relay.flush_once())

    row = storage.get_outbound(event_id)
    assert row["status"] == "retrying"
    assert "ConnectError" in row["last_error"]
    # Срок повтора ещё не наступил — событие не в выборке due.
    assert storage.due_outbound(time.time()) == []


def test_lifecycle_trigger_builds_contract_envelope(
    app, client, signed_post, enrollment_event
) -> None:
    enr_id = signed_post("/api/v1/enrollments", enrollment_event()).json()["lms_enrollment_id"]

    response = client.post(
        f"/demo/enrollments/{enr_id}/lifecycle",
        data={"kind": "completed"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    outbound = [e for e in app.state.storage.journal() if e["direction"] == "out"]
    assert len(outbound) == 1
    envelope = json.loads(outbound[0]["envelope"])
    assert envelope["event_type"] == "lms.learning.completed"
    assert envelope["correlation"]["crm_request_id"] == "rq-0001"
    assert envelope["correlation"]["lms_enrollment_id"] == enr_id
    payload = envelope["payload"]
    assert payload["status"] == {"code": "completed", "name": "Обучение завершено"}
    assert payload["university"]["name"] == "МФТИ"
    assert payload["links"]["s3_keys"] == [f"lms-exports/{enr_id}/final_report.pdf"]
    # Состояние зачисления обновилось.
    enrollment = client.get("/api/v1/enrollments").json()["items"][0]
    assert enrollment["lifecycle"] == "completed"


def test_progress_event_carries_percent(app, client, signed_post, enrollment_event) -> None:
    enr_id = signed_post("/api/v1/enrollments", enrollment_event()).json()["lms_enrollment_id"]
    client.post(
        f"/demo/enrollments/{enr_id}/lifecycle",
        data={"kind": "progress"},
        follow_redirects=False,
    )
    outbound = [e for e in app.state.storage.journal() if e["direction"] == "out"]
    envelope = json.loads(outbound[0]["envelope"])
    assert envelope["event_type"] == "lms.learning.progress"
    assert envelope["payload"]["progress"] == {"percent": 50}


def test_lifecycle_for_unknown_enrollment_404(client) -> None:
    response = client.post(
        "/demo/enrollments/lms-enr-9999/lifecycle",
        data={"kind": "started"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
