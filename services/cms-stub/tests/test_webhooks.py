"""Приём каталога из CRM: HMAC, идемпотентность, публикация/снятие."""
from __future__ import annotations

from app.security import sign_body


def test_hmac_known_vector() -> None:
    # Контрольное значение посчитано независимо: HMAC_SHA256("secret", b"{}").
    assert (
        sign_body("secret", b"{}")
        == "sha256=77325902caca812dc259733aacd046b73817372c777b8d95b402647474516e13"
    )


def test_catalog_upsert_assigns_external_id(signed_post, publish_event, client) -> None:
    response = signed_post("/api/v1/catalog/upsert", publish_event())
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["duplicate"] is False
    assert body["cms_external_id"].startswith("cms-item-")
    catalog = client.get("/api/v1/catalog").json()
    assert catalog["total"] == 1
    assert catalog["items"][0]["name"] == "Практикум по ML, весна-2027"
    assert catalog["items"][0]["product"]["code"] == "dpo-ds"


def test_invalid_signature_rejected(signed_post, publish_event) -> None:
    response = signed_post("/api/v1/catalog/upsert", publish_event(), secret="wrong")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_duplicate_event_id_is_idempotent(signed_post, publish_event, client) -> None:
    event = publish_event()
    first = signed_post("/api/v1/catalog/upsert", event).json()
    second = signed_post("/api/v1/catalog/upsert", event).json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["cms_external_id"] == first["cms_external_id"]
    assert client.get("/api/v1/catalog").json()["total"] == 1


def test_reupsert_keeps_external_id(signed_post, publish_event) -> None:
    first = signed_post("/api/v1/catalog/upsert", publish_event()).json()
    second = signed_post(
        "/api/v1/catalog/upsert", publish_event(name="Обновлённое название")
    ).json()
    assert second["duplicate"] is False  # другой event_id
    assert second["cms_external_id"] == first["cms_external_id"]


def test_unpublish_hides_from_catalog(signed_post, publish_event, client) -> None:
    upserted = signed_post("/api/v1/catalog/upsert", publish_event()).json()
    response = signed_post(
        "/api/v1/catalog/unpublish", publish_event(event_type="crm.program.unpublished")
    )
    assert response.status_code == 200
    assert response.json()["cms_external_id"] == upserted["cms_external_id"]
    assert client.get("/api/v1/catalog").json()["total"] == 0


def test_unpublish_unknown_program_404(signed_post, publish_event) -> None:
    response = signed_post(
        "/api/v1/catalog/unpublish",
        publish_event(event_type="crm.program.unpublished", crm_program_id="pg-nope"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_wrong_event_type_rejected(signed_post, publish_event) -> None:
    response = signed_post(
        "/api/v1/catalog/upsert", publish_event(event_type="crm.program.unpublished")
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_events_journal_and_health(client, signed_post, publish_event) -> None:
    signed_post("/api/v1/catalog/upsert", publish_event())
    events = client.get("/api/events").json()
    assert events["total"] == 1
    assert events["items"][0]["direction"] == "in"
    assert events["items"][0]["event_type"] == "crm.program.published"
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_index_page_renders(client, signed_post, publish_event) -> None:
    signed_post("/api/v1/catalog/upsert", publish_event())
    response = client.get("/")
    assert response.status_code == 200
    assert "CMS-заглушка" in response.text
    assert "Практикум по ML" in response.text
