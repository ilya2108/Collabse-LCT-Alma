"""Фикстуры cms-stub: приложение с временной SQLite и выключенным авто-relay."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import sign_body

TEST_SECRET = "test-cms-secret"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        webhook_secret=TEST_SECRET,
        crm_webhook_url="http://crm.test/api/v1/integrations/cms/webhook",
        db_path=str(tmp_path / "cms-stub.sqlite3"),
        retry_schedule=(0, 0),
        relay_auto_start=False,
    )


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings=settings)


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def signed_post(client: TestClient) -> Callable[..., httpx.Response]:
    """POST конверта с корректной (или произвольной) HMAC-подписью."""

    def _post(path: str, envelope: dict[str, Any], secret: str = TEST_SECRET) -> httpx.Response:
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        return client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(secret, body),
            },
        )

    return _post


@pytest.fixture()
def publish_event() -> Callable[..., dict[str, Any]]:
    """Конверт crm.program.published / crm.program.unpublished (api-contract §13.4)."""

    def _make(
        *,
        event_id: str | None = None,
        event_type: str = "crm.program.published",
        crm_program_id: str = "pg-0001",
        cms_external_id: str | None = None,
        name: str = "Практикум по ML, весна-2027",
    ) -> dict[str, Any]:
        return {
            "event_id": event_id or str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": "2026-09-16T12:34:56Z",
            "source": "crm",
            "correlation": {
                "crm_request_id": None,
                "lms_enrollment_id": None,
                "cms_lead_id": None,
            },
            "payload": {
                "status": None,
                "responsible": None,
                "university": None,
                "program": {
                    "crm_program_id": crm_program_id,
                    "name": name,
                    "cms_external_id": cms_external_id,
                    "lms_course_id": None,
                },
                "product": {
                    "crm_product_id": "pr-0001",
                    "code": "dpo-ds",
                    "name": "ДПО: Data Science",
                },
                "links": {"db_keys": {"program_id": crm_program_id}, "s3_keys": []},
            },
        }

    return _make
