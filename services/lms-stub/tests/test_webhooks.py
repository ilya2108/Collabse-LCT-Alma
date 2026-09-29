"""Приём push из CRM: HMAC, идемпотентность, побочные эффекты."""
from __future__ import annotations

import json

from app.security import sign_body


def test_hmac_known_vector() -> None:
    # Контрольное значение посчитано независимо: HMAC_SHA256("secret", b"{}").
    assert (
        sign_body("secret", b"{}")
        == "sha256=77325902caca812dc259733aacd046b73817372c777b8d95b402647474516e13"
    )


def test_enrollment_accepted_with_valid_signature(signed_post, enrollment_event) -> None:
    response = signed_post("/api/v1/enrollments", enrollment_event())
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["duplicate"] is False
    assert body["lms_enrollment_id"].startswith("lms-enr-")
    assert body["received_event_id"]


def test_invalid_signature_rejected(signed_post, enrollment_event) -> None:
    response = signed_post("/api/v1/enrollments", enrollment_event(), secret="wrong-secret")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_missing_signature_rejected(client, enrollment_event) -> None:
    response = client.post("/api/v1/enrollments", json=enrollment_event())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_duplicate_event_id_is_idempotent(signed_post, enrollment_event, client) -> None:
    event = enrollment_event()
    first = signed_post("/api/v1/enrollments", event).json()
    second = signed_post("/api/v1/enrollments", event).json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["lms_enrollment_id"] == first["lms_enrollment_id"]
    # Побочный эффект не повторился: зачисление одно.
    enrollments = client.get("/api/v1/enrollments").json()
    assert enrollments["total"] == 1


def test_same_request_reuses_enrollment_id(signed_post, enrollment_event) -> None:
    first = signed_post("/api/v1/enrollments", enrollment_event(crm_request_id="rq-42")).json()
    second = signed_post("/api/v1/enrollments", enrollment_event(crm_request_id="rq-42")).json()
    assert second["duplicate"] is False  # другой event_id
    assert second["lms_enrollment_id"] == first["lms_enrollment_id"]


def test_wrong_event_type_rejected(signed_post, program_event) -> None:
    response = signed_post("/api/v1/enrollments", program_event())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_invalid_envelope_rejected(client, settings) -> None:
    body = json.dumps({"event_type": "crm.request.handed_over"}).encode()
    response = client.post(
        "/api/v1/enrollments",
        content=body,
        headers={"X-Webhook-Signature": sign_body(settings.webhook_secret, body)},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(item["field"] == "event_id" for item in error["details"])


def test_enrollment_without_request_id_unprocessable(signed_post, enrollment_event) -> None:
    event = enrollment_event()
    event["correlation"]["crm_request_id"] = None
    event["payload"]["links"]["db_keys"] = {}
    response = signed_post("/api/v1/enrollments", event)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


def test_program_upsert_assigns_course_id(signed_post, program_event) -> None:
    body = signed_post("/api/v1/programs/upsert", program_event()).json()
    assert body["accepted"] is True
    assert body["lms_course_id"].startswith("lms-course-")


def test_program_upsert_keeps_known_course_id(signed_post, program_event) -> None:
    first = signed_post("/api/v1/programs/upsert", program_event()).json()
    second = signed_post(
        "/api/v1/programs/upsert", program_event(name="Обновлённое имя")
    ).json()
    assert second["lms_course_id"] == first["lms_course_id"]


def test_events_journal_and_health(client, signed_post, enrollment_event) -> None:
    signed_post("/api/v1/enrollments", enrollment_event())
    events = client.get("/api/events").json()
    assert events["total"] == 1
    entry = events["items"][0]
    assert entry["direction"] == "in"
    assert entry["event_type"] == "crm.request.handed_over"
    assert entry["envelope"]["payload"]["university"]["name"] == "МФТИ"
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_index_page_renders(client, signed_post, enrollment_event) -> None:
    signed_post("/api/v1/enrollments", enrollment_event())
    response = client.get("/")
    assert response.status_code == 200
    assert "LMS-заглушка" in response.text
    assert "crm.request.handed_over" in response.text
