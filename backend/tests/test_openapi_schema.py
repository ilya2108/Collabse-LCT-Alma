"""Swagger `/api/v1/docs`: ErrorEnvelope, 400 вместо 422 FastAPI, стандартные ответы,
Out-модели и вебхуки (core/openapi.py, находка критики P1-4, ТР2 «подробно описаны»)."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ERROR_CODES, ErrorEnvelope, RequestIdMiddleware, register_error_handlers
from app.core.idempotency import DEFAULT_EXCLUDED_PATHS, install_idempotency_openapi
from app.core.openapi import (
    REF_PREFIX,
    Items,
    Page,
    Wrapped,
    _documented_responses,
    _spec_model,
    collect_definitions,
    install_openapi_postprocessing,
    postprocess_openapi,
)
from app.main import create_app

ERROR_REF = REF_PREFIX + "ErrorEnvelope"
HTTP_METHODS = {"get", "put", "post", "delete", "patch"}
MUTATING = {"post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """Схема настоящего приложения (строится ~0,5 с — одна на модуль)."""
    return create_app().openapi()


def _operations(schema: dict[str, Any]):
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def _json_schema(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("content", {}).get("application/json", {}).get("schema", {})


# --- ошибки --------------------------------------------------------------------------------


def test_fastapi_validation_schema_is_gone(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    assert "HTTPValidationError" not in components
    assert "ValidationError" not in components
    assert "HTTPValidationError" not in json.dumps(schema)
    for name in ("ErrorEnvelope", "ErrorBody", "ErrorDetail"):
        assert name in components, name


def test_every_error_response_uses_error_envelope(schema: dict[str, Any]) -> None:
    checked = 0
    for path, method, operation in _operations(schema):
        if not path.startswith("/api/v1/"):
            continue
        assert "default" in operation["responses"], (method, path)
        for status, response in operation["responses"].items():
            if status == "default" or status[0] in "45":
                assert _json_schema(response) == {"$ref": ERROR_REF}, (method, path, status)
                assert response["description"], (method, path, status)
                checked += 1
    assert checked > 600


def test_validation_errors_are_400_not_fastapi_422(schema: dict[str, Any]) -> None:
    with_input = 0
    for path, method, operation in _operations(schema):
        responses = operation["responses"]
        parameters = operation.get("parameters", [])
        # заголовок Idempotency-Key FastAPI не проверяет — его формат проверяет middleware
        validated = [p for p in parameters if p["name"] != "Idempotency-Key"]
        if validated or operation.get("requestBody"):
            with_input += 1
            assert "400" in responses, (method, path)
            assert "`validation_error`" in responses["400"]["description"], (method, path)
        if len(validated) < len(parameters):
            assert "`idempotency_key_invalid`" in responses["400"]["description"], (method, path)
        if "422" in responses:
            description = responses["422"]["description"]
            assert not description.startswith("Validation Error"), (method, path)
            # 422 остаётся только с собственным описанием: Idempotency-Key или unprocessable
            assert "idempotency_key_reused" in description or "unprocessable" in description
    assert with_input > 120


def test_standard_responses(schema: dict[str, Any]) -> None:
    for path, method, operation in _operations(schema):
        if not path.startswith("/api/v1/"):
            continue
        responses = operation["responses"]
        names = {p["name"].lower() for p in operation.get("parameters", []) if p["in"] == "header"}
        if operation.get("security"):
            assert {"401", "403"} <= set(responses), (method, path)
        elif "x-internal-token" in names:
            assert "401" in responses, (method, path)
        if "{" in path:
            assert "404" in responses, (method, path)
        if method in MUTATING:
            assert "409" in responses, (method, path)
        if "multipart/form-data" in (operation.get("requestBody") or {}).get("content", {}):
            assert "413" in responses, (method, path)
        statuses = [s for s in responses if s != "default"]
        assert statuses == sorted(statuses, key=int), (method, path)  # 2xx, затем 4xx, 5xx


def test_router_descriptions_are_kept(schema: dict[str, Any]) -> None:
    dashboard = schema["paths"]["/api/v1/reports/dashboard"]["get"]["responses"]["400"]
    assert dashboard["description"].startswith("Неверный id")
    assert "`validation_error`" in dashboard["description"]
    create = schema["paths"]["/api/v1/requests"]["post"]["responses"]
    # описания Idempotency-Key на месте, схема ошибки добавлена
    assert "idempotency_in_progress" in create["409"]["description"]
    assert "`stale_version`" in create["409"]["description"]
    assert "idempotency_key_reused" in create["422"]["description"]
    assert ".." not in create["409"]["description"]


def test_error_envelope_lists_catalog(schema: dict[str, Any]) -> None:
    description = schema["components"]["schemas"]["ErrorEnvelope"]["description"]
    for code, (status, _) in ERROR_CODES.items():
        assert f"`{status}` `{code}`" in description, code
    body = schema["components"]["schemas"]["ErrorBody"]
    assert set(body["required"]) == {"code", "message", "trace_id"}


def test_all_refs_resolve(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    refs = set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(schema)))
    assert refs - set(components) == set()


# --- успешные ответы: существующие Out-модели ---------------------------------------------


def test_documented_responses_applied(schema: dict[str, Any]) -> None:
    documented = _documented_responses()
    assert len(documented) >= 20
    for (method, path), spec in documented.items():
        operation = schema["paths"][path][method]
        target = _json_schema(operation["responses"][spec.status])
        assert "$ref" in json.dumps(target), (method, path)
    request = schema["paths"]["/api/v1/requests/{request_id}"]["get"]["responses"]["200"]
    assert _json_schema(request) == {"$ref": REF_PREFIX + "RequestOut"}
    page = schema["components"]["schemas"]["RequestOutPage"]
    assert page["required"] == ["items", "total", "limit", "offset"]
    impact = schema["paths"]["/api/v1/workflows/{workflow_id}/impact-preview"]["post"]
    assert _json_schema(impact["responses"]["200"])["properties"]["impact"] == {
        "$ref": REF_PREFIX + "ImpactOut"
    }
    approve = schema["paths"]["/api/v1/workflows/changes/{change_id}/approve"]["post"]
    assert _json_schema(approve["responses"]["200"]) == {"$ref": REF_PREFIX + "ApplyResultOut"}
    # отчёт переноса — с алиасами сериализации from/to, как в ответе
    by_status = schema["components"]["schemas"]["MigrationReportByStatus"]["properties"]
    assert {"from", "to", "count"} == set(by_status)


def test_documented_models_have_no_name_conflicts(schema: dict[str, Any]) -> None:
    models = [(_spec_model(d.spec), "serialization") for d in _documented_responses().values()]
    definitions, _, conflicts = collect_definitions(dict.fromkeys(models))
    assert conflicts == []
    # схема FastAPI под тем же именем (та же модель из response_model) — те же поля
    components = schema["components"]["schemas"]
    for name, body in definitions.items():
        assert set(components[name].get("properties", {})) == set(body.get("properties", {})), name


def test_documented_shapes_match_handlers() -> None:
    # сверка формы-обёртки с тем, что возвращает ручка (list_envelope / items+total / impact)
    documented = _documented_responses()
    assert isinstance(documented[("get", "/api/v1/requests")].spec, Page)
    assert isinstance(documented[("get", "/api/v1/requests/{request_id}/comments")].spec, Items)
    impact = documented[("post", "/api/v1/workflows/{workflow_id}/impact-preview")].spec
    assert isinstance(impact, Wrapped) and impact.key == "impact"


# --- вебхуки --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "ok_statuses", "ack"),
    [
        ("/api/v1/integrations/lms/webhook", {"200"}, "LmsWebhookAck"),
        ("/api/v1/integrations/cms/webhook", {"200", "201"}, "CmsWebhookAck"),
    ],
)
def test_webhooks_documented(
    schema: dict[str, Any], path: str, ok_statuses: set[str], ack: str
) -> None:
    operation = schema["paths"][path]["post"]
    body = operation["requestBody"]["content"]["application/json"]
    assert body["schema"] == {"$ref": REF_PREFIX + "InboundEnvelope"}
    assert body["example"]["event_id"]
    headers = {p["name"]: p for p in operation["parameters"] if p["in"] == "header"}
    assert headers["X-Webhook-Signature"]["required"] is False
    assert "Idempotency-Key" not in headers  # у вебхуков своя идемпотентность по event_id
    assert path in DEFAULT_EXCLUDED_PATHS
    responses = operation["responses"]
    for status in ok_statuses:
        assert _json_schema(responses[status]) == {"$ref": REF_PREFIX + ack}
    assert {"400", "401", "409", "422"} <= set(responses)
    assert "`unauthorized`" in responses["401"]["description"]
    assert "`conflict`" in responses["409"]["description"]
    assert "Конфликт данных" not in responses["409"]["description"]  # без общего текста


def test_inbound_envelope_component(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    envelope = components["InboundEnvelope"]
    assert set(envelope["required"]) == {"event_id", "event_type", "occurred_at", "source"}
    payload = envelope["properties"]["payload"]
    assert {"status", "responsible", "university", "program", "product", "links", "lead"} <= set(
        payload["properties"]
    )
    assert "LeadBlock" in components and "Correlation" in components


def test_inbound_envelope_parsing_unchanged() -> None:
    from app.modules.integrations.schemas import InboundEnvelope

    parsed = InboundEnvelope.model_validate(
        {
            "event_id": "b2c3d4e5-6f70-4a1b-8c2d-3e4f5a6b7c8d",
            "event_type": "cms.lead.created",
            "occurred_at": "2026-09-17T15:41:00Z",
            "source": "cms",
            "payload": {"lead": {"client_kind": "person"}, "custom": 1},
        }
    )
    # payload по-прежнему словарь: неизвестные блоки не теряются, сервис читает .get()
    assert parsed.payload == {"lead": {"client_kind": "person"}, "custom": 1}


# --- обёртка: идемпотентность и мини-приложение --------------------------------------------


def test_postprocessing_is_idempotent() -> None:
    app = create_app()
    first = copy.deepcopy(app.openapi())
    assert app.openapi() == first  # повторный вызов (кэш + обе обёртки) ничего не меняет
    again = postprocess_openapi(copy.deepcopy(first))
    assert again == first


class _Body(BaseModel):
    name: str


def _mini_app() -> FastAPI:
    app = FastAPI(openapi_url="/api/v1/openapi.json")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.post("/api/v1/things", status_code=201)
    async def create_thing(body: _Body) -> dict[str, Any]:
        return {"name": body.name}

    @app.get("/api/v1/things/{thing_id}")
    async def get_thing(thing_id: int) -> dict[str, Any]:
        return {"id": thing_id}

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, Any]:
        return {}

    install_idempotency_openapi(app)
    install_openapi_postprocessing(app)
    return app


def test_mini_app_schema_matches_runtime() -> None:
    app = _mini_app()
    spec = app.openapi()
    create = spec["paths"]["/api/v1/things"]["post"]["responses"]
    assert set(create) == {"201", "400", "409", "422", "default"}
    assert "Validation Error" not in json.dumps(create)
    get = spec["paths"]["/api/v1/things/{thing_id}"]["get"]["responses"]
    assert set(get) == {"200", "400", "404", "default"}  # 422 FastAPI без своего текста — убран
    ping = spec["paths"]["/api/v1/ping"]["get"]["responses"]
    assert set(ping) == {"200", "default"}  # без параметров — без 400

    # сервер отвечает ровно тем, что обещает схема: 400 в форме ErrorEnvelope
    client = TestClient(app)
    response = client.post("/api/v1/things", json={})
    assert response.status_code == 400
    assert ErrorEnvelope.model_validate(response.json()).error.code == "validation_error"
    missing = client.get("/api/v1/things/abc")
    assert missing.status_code == 400
