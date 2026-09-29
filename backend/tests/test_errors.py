"""Единый формат ошибок {error: {code, message, details, trace_id}} (api-contract.md §1.4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import (
    ApiError,
    RequestIdMiddleware,
    conflict,
    not_found,
    register_error_handlers,
    stale_version,
    validation_error,
)


class _Body(BaseModel):
    email: str
    amount: int


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom-domain")
    async def boom_domain() -> None:
        raise ApiError(409, "workflow_invalid_transition", "Переход не разрешён схемой workflow")

    @app.post("/validated")
    async def validated(body: _Body) -> dict:
        return {"ok": True}

    @app.get("/boom-unexpected")
    async def boom_unexpected() -> None:
        raise RuntimeError("секретные детали: пароль=hunter2")

    return TestClient(app, raise_server_exceptions=False)


def test_api_error_envelope(client: TestClient) -> None:
    response = client.get("/boom-domain")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "workflow_invalid_transition"
    assert body["error"]["message"] == "Переход не разрешён схемой workflow"
    assert body["error"]["trace_id"]


def test_pydantic_validation_is_400_with_details(client: TestClient) -> None:
    # контракт: ошибки валидации — 400 validation_error, не 422 FastAPI
    response = client.post("/validated", json={"email": 1})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Ошибка валидации данных"
    fields = {d["field"] for d in body["error"]["details"]}
    assert "amount" in fields


def test_unknown_route_is_wrapped_404(client: TestClient) -> None:
    response = client.get("/no-such-route")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "Объект не найден"


def test_trace_id_echoes_request_id_header(client: TestClient) -> None:
    rid = "11111111-2222-3333-4444-555555555555"
    response = client.get("/boom-domain", headers={"X-Request-Id": rid})
    assert response.headers["X-Request-Id"] == rid
    assert response.json()["error"]["trace_id"] == rid


def test_unexpected_exception_hides_details(client: TestClient) -> None:
    response = client.get("/boom-unexpected")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text


def test_error_factories() -> None:
    assert (not_found().status_code, not_found().code) == (404, "not_found")
    assert (conflict().status_code, conflict().code) == (409, "conflict")
    assert (stale_version().status_code, stale_version().code) == (409, "stale_version")
    err = validation_error(details=[{"field": "email", "code": "invalid_format", "message": "x"}])
    assert err.status_code == 400 and err.details is not None
