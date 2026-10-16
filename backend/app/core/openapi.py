"""Постобработка схемы OpenAPI (Swagger `/api/v1/docs`): ошибки, стандартные ответы,
схемы успешных ответов.

Роутеры описывают свои ручки; общее для всех — здесь, одной обёрткой `app.openapi`,
без правки каждого роутера и без изменения логики ручек:

- `ErrorEnvelope` (core/errors.py) в components, у каждого ответа 4xx/5xx под `/api/v1/` —
  эта схема (api-contract.md §1.4); каталог кодов — в описании схемы;
- ошибки валидации: сервер отвечает `400 validation_error` (core/errors.py), а FastAPI
  обещает `422 HTTPValidationError`. Такой 422 заменяется на 400; 422 остаётся только
  с собственным описанием (`idempotency_key_reused` у мутаций), а HTTPValidationError
  уходит из components;
- стандартные ответы: 401/403 — у операций с Bearer, 401 — с `X-Internal-Token`,
  404 — у путей с параметром, 409 — у мутаций, 413 — у загрузки файлов, `default` —
  прочие ошибки в том же конверте. Описание, которое задал роутер, сохраняется;
- ручки, которые отдают дамп существующей Out-модели, но объявлены как
  `dict[str, Any]` (в Swagger — пустой object), получают схему этой модели
  (`_documented_responses`); если роутер задаст схему сам, его схема главнее;
- модели, на которые роутер ссылается вручную в `openapi_extra` (тело вебхука), —
  через `schema_ref()`.

Обёртка идемпотентна и ставится последней, после Idempotency-Key (core/idempotency.py):
она видит его описания 409/422 и добавляет к ним схему ошибки.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from app.core.errors import ERROR_CODES, ErrorEnvelope

logger = logging.getLogger("app.openapi")

REF_PREFIX = "#/components/schemas/"
API_PREFIX = "/api/v1/"
JSON = "application/json"

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_MUTATING_METHODS = frozenset({"post", "put", "patch", "delete"})

# 422 FastAPI по умолчанию: описание и схемы, которые сервер на деле не отдаёт
_FASTAPI_422_DESCRIPTION = "Validation Error"
_FASTAPI_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")
_FASTAPI_SUCCESS_DESCRIPTION = "Successful Response"

# стандартные описания: (статус, код-маркер, текст). Маркер уже есть в описании
# роутера — текст не дописывается (роутер описал этот ответ сам)
_TEXT_400 = (
    "Ошибка валидации тела или параметров — `validation_error`, в `details` поле и причина "
    "(сервер отвечает 400, а не 422 FastAPI)"
)
_TEXT_400_IDEMPOTENCY = "Заголовок `Idempotency-Key` не по формату — `idempotency_key_invalid`"
_TEXT_401 = "Нет токена или токен просрочен — `unauthorized`"
_TEXT_401_INTERNAL = "Неверный или отсутствующий `X-Internal-Token` — `unauthorized`"
_TEXT_403 = (
    "Нет права, процесс B2B/B2C недоступен роли или учётная запись деактивирована — "
    "`forbidden` (в `message` — название права)"
)
_TEXT_404 = "Объект не найден или вне области роли (существование не раскрываем) — `not_found`"
_TEXT_409 = (
    "Конфликт данных — `conflict` (уникальность, состояние объекта не допускает операцию) "
    "или `stale_version` (передан устаревший `version`)"
)
_TEXT_413 = "Файл больше допустимого размера — `file_too_large`"
_TEXT_DEFAULT = (
    "Прочие ошибки — тот же конверт: 422 `unprocessable`, 500 `internal_error`, "
    "502 `integration_unavailable`, 503 `service_unavailable`. Каталог кодов — в схеме "
    "ErrorEnvelope"
)


# --------------------------------------------------------------------------------------
# Модели в components
# --------------------------------------------------------------------------------------

JsonSchemaMode = Literal["validation", "serialization"]

# модели из `openapi_extra` роутеров: имя → (модель, режим); заполняется при импорте роутеров
_REGISTERED: dict[str, tuple[type[BaseModel], JsonSchemaMode]] = {}


def schema_ref(model: type[BaseModel], mode: JsonSchemaMode = "validation") -> dict[str, str]:
    """`{"$ref": ...}` на модель для `openapi_extra` роутера (тело, которое ручка читает
    сама, например ради HMAC по сырому телу). Схема модели и вложенных попадёт в
    components при постобработке."""
    _REGISTERED[model.__name__] = (model, mode)
    return {"$ref": REF_PREFIX + model.__name__}


def collect_definitions(
    models: Iterable[tuple[type[BaseModel], JsonSchemaMode]],
) -> tuple[dict[str, dict[str, Any]], dict[type[BaseModel], dict[str, str]], list[str]]:
    """JSON Schema моделей и вложенных: (определения, ссылка на каждую модель, конфликты).

    Конфликт — два разных определения под одним именем (одноимённые классы с разными
    полями); остаётся первое, имя попадает в список (тест требует, чтобы он был пуст).
    """
    definitions: dict[str, dict[str, Any]] = {}
    refs: dict[type[BaseModel], dict[str, str]] = {}
    conflicts: list[str] = []
    for model, mode in models:
        key_map, generated = models_json_schema(
            [(model, mode)], ref_template=REF_PREFIX + "{model}"
        )
        refs[model] = dict(key_map[(model, mode)])
        for name, body in generated.get("$defs", {}).items():
            known = definitions.setdefault(name, body)
            if known != body and name not in conflicts:
                conflicts.append(name)
    return definitions, refs, conflicts


# --------------------------------------------------------------------------------------
# Успешные ответы: существующие Out-модели у ручек с `-> dict[str, Any]`
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """Конверт списка с пагинацией (§1.5): items, total, limit, offset."""

    model: type[BaseModel]


@dataclass(frozen=True)
class Items:
    """Список без пагинации: items и total."""

    model: type[BaseModel]


@dataclass(frozen=True)
class Wrapped:
    """Объект с одним ключом: `{"<key>": <модель>}`."""

    key: str
    model: type[BaseModel]


ResponseSpec = type[BaseModel] | Page | Items | Wrapped


@dataclass(frozen=True)
class DocumentedResponse:
    status: str
    spec: ResponseSpec
    note: str | None = None


def _documented_responses() -> dict[tuple[str, str], DocumentedResponse]:
    """(метод, путь) → схема успешного ответа. Только ручки, которые отдают
    `<Out-модель>.model_dump(...)`; сверка с кодом — tests/test_openapi_schema.py."""
    # импорт здесь: core не тянет модули при импорте (порядок импорта роутеров)
    from app.modules.crm.schemas import (
        ContractOut,
        DirectionOut,
        ProductOut,
        ProgramOut,
        UniversityOut,
    )
    from app.modules.requests.schemas import CommentOut, RequestFileOut, RequestOut
    from app.modules.talent_pool.schemas import ActivityOut
    from app.modules.workflow.schemas import ApplyResultOut, ChangeRequestOut, ImpactOut

    request_id = "/api/v1/requests/{request_id}"
    change_id = "/api/v1/workflows/changes/{change_id}"
    return {
        ("get", "/api/v1/requests"): DocumentedResponse("200", Page(RequestOut)),
        ("post", "/api/v1/requests"): DocumentedResponse(
            "201",
            RequestOut,
            "Заявка создана. При возможном дубле в ответе есть строка `warning_duplicate`",
        ),
        ("get", request_id): DocumentedResponse("200", RequestOut),
        ("patch", request_id): DocumentedResponse("200", RequestOut),
        ("post", f"{request_id}/assign"): DocumentedResponse("200", RequestOut),
        ("post", f"{request_id}/transitions"): DocumentedResponse("200", RequestOut),
        ("post", f"{request_id}/reopen"): DocumentedResponse("200", RequestOut),
        ("get", f"{request_id}/comments"): DocumentedResponse("200", Items(CommentOut)),
        ("post", f"{request_id}/comments"): DocumentedResponse("201", CommentOut),
        ("get", f"{request_id}/files"): DocumentedResponse("200", Items(RequestFileOut)),
        ("post", f"{request_id}/files"): DocumentedResponse("201", RequestFileOut),
        ("post", "/api/v1/workflows/{workflow_id}/impact-preview"): DocumentedResponse(
            "200", Wrapped("impact", ImpactOut)
        ),
        ("post", "/api/v1/workflows/{workflow_id}/changes"): DocumentedResponse(
            "201", ChangeRequestOut
        ),
        ("get", "/api/v1/workflows/{workflow_id}/changes"): DocumentedResponse(
            "200", Page(ChangeRequestOut)
        ),
        ("get", change_id): DocumentedResponse("200", ChangeRequestOut),
        ("post", f"{change_id}/approve"): DocumentedResponse("200", ApplyResultOut),
        ("post", f"{change_id}/reject"): DocumentedResponse("200", ChangeRequestOut),
        ("get", "/api/v1/universities"): DocumentedResponse("200", Page(UniversityOut)),
        ("get", "/api/v1/contracts"): DocumentedResponse(
            "200", Page(ContractOut), "Список без вложенных файлов (`files` = null)"
        ),
        ("get", "/api/v1/it-directions"): DocumentedResponse("200", Page(DirectionOut)),
        ("get", "/api/v1/products"): DocumentedResponse("200", Page(ProductOut)),
        ("get", "/api/v1/programs"): DocumentedResponse("200", Page(ProgramOut)),
        ("get", "/api/v1/activities"): DocumentedResponse("200", Page(ActivityOut)),
    }


def _spec_model(spec: ResponseSpec) -> type[BaseModel]:
    return spec if isinstance(spec, type) else spec.model


def _spec_schema(
    spec: ResponseSpec,
    refs: dict[type[BaseModel], dict[str, str]],
    components: dict[str, Any],
) -> dict[str, Any]:
    ref = refs[_spec_model(spec)]
    if isinstance(spec, type):
        return dict(ref)
    if isinstance(spec, Wrapped):
        return {"type": "object", "required": [spec.key], "properties": {spec.key: dict(ref)}}
    name = spec.model.__name__ + ("Page" if isinstance(spec, Page) else "List")
    if name not in components:
        properties: dict[str, Any] = {
            "items": {"type": "array", "items": dict(ref)},
            "total": {"type": "integer", "description": "Всего записей"},
        }
        if isinstance(spec, Page):
            properties["limit"] = {"type": "integer"}
            properties["offset"] = {"type": "integer"}
        components[name] = {
            "title": name,
            "type": "object",
            "required": list(properties),
            "properties": properties,
        }
    return {"$ref": REF_PREFIX + name}


def _is_untyped(schema: dict[str, Any]) -> bool:
    """Схема `dict[str, Any]` FastAPI: object без ссылок и без описанных полей."""
    return (
        schema.get("type", "object") == "object"
        and "properties" not in schema
        and "$ref" not in json.dumps(schema)
    )


def _document_success(
    responses: dict[str, Any],
    documented: DocumentedResponse,
    refs: dict[type[BaseModel], dict[str, str]],
    components: dict[str, Any],
) -> None:
    response = responses.get(documented.status)
    if response is None:
        return
    content = response.setdefault("content", {}).setdefault(JSON, {})
    current = content.get("schema") or {}
    if not _is_untyped(current):
        return  # роутер задал схему сам — его схема главнее
    content["schema"] = _spec_schema(documented.spec, refs, components)
    description = str(response.get("description") or "")
    if documented.note and documented.note not in description:
        keep = description and description != _FASTAPI_SUCCESS_DESCRIPTION
        response["description"] = f"{description}. {documented.note}" if keep else documented.note


# --------------------------------------------------------------------------------------
# Ошибки
# --------------------------------------------------------------------------------------


def error_codes_markdown() -> str:
    """Каталог кодов для описания ErrorEnvelope: по строке на код, по возрастанию статуса."""
    rows = sorted(ERROR_CODES.items(), key=lambda item: (item[1][0], item[0]))
    return "\n".join(f"- `{status}` `{code}` — {text}" for code, (status, text) in rows)


def _error_content() -> dict[str, Any]:
    return {JSON: {"schema": {"$ref": REF_PREFIX + ErrorEnvelope.__name__}}}


def _is_error_status(status: str) -> bool:
    return status == "default" or (status[:1] in {"4", "5"})


def _refers_to_validation_error(response: dict[str, Any]) -> bool:
    return any(
        f"{REF_PREFIX}{name}" in json.dumps(response.get("content") or {})
        for name in _FASTAPI_VALIDATION_SCHEMAS
    )


def _merge(responses: dict[str, Any], status: str, text: str, marker: str | None = None) -> None:
    """Добавляет ответ или дописывает текст к описанию роутера (если код ещё не назван)."""
    response = responses.get(status)
    if response is None:
        responses[status] = {"description": text}
        return
    description = str(response.get("description") or "")
    if text in description or (marker and marker in description):
        return
    # чужой текст не правим (Idempotency-Key ищет в описании свою фразу с точкой)
    separator = " " if description.endswith(".") else ". "
    response["description"] = f"{description}{separator}{text}" if description else text


def _replace_fastapi_422(responses: dict[str, Any]) -> None:
    """422 HTTPValidationError → 400 validation_error; у 422 остаётся только своё описание."""
    response = responses.get("422")
    if response is None or not _refers_to_validation_error(response):
        return
    description = str(response.get("description") or "")
    rest = description.removeprefix(_FASTAPI_422_DESCRIPTION).lstrip(". ").strip()
    if rest:
        response["description"] = rest
        response["content"] = _error_content()
    else:
        del responses["422"]
    _merge(responses, "400", _TEXT_400, "`validation_error`")


def _has_header(operation: dict[str, Any], name: str) -> bool:
    return any(
        param.get("in") == "header" and str(param.get("name", "")).lower() == name.lower()
        for param in operation.get("parameters") or []
    )


def _is_upload(operation: dict[str, Any]) -> bool:
    content = (operation.get("requestBody") or {}).get("content") or {}
    return "multipart/form-data" in content


def _add_standard_responses(path: str, method: str, operation: dict[str, Any]) -> None:
    responses = operation.setdefault("responses", {})
    _replace_fastapi_422(responses)
    if _has_header(operation, "Idempotency-Key"):
        _merge(responses, "400", _TEXT_400_IDEMPOTENCY, "`idempotency_key_invalid`")
    if operation.get("security"):
        _merge(responses, "401", _TEXT_401, "`unauthorized`")
        _merge(responses, "403", _TEXT_403, "`forbidden`")
    elif _has_header(operation, "X-Internal-Token"):
        _merge(responses, "401", _TEXT_401_INTERNAL, "`unauthorized`")
    if "{" in path:
        _merge(responses, "404", _TEXT_404, "`not_found`")
    if method in _MUTATING_METHODS:
        _merge(responses, "409", _TEXT_409, "`conflict`")
    if _is_upload(operation):
        _merge(responses, "413", _TEXT_413, "`file_too_large`")
    _merge(responses, "default", _TEXT_DEFAULT)
    for status, response in responses.items():
        if _is_error_status(status) and isinstance(response, dict) and "content" not in response:
            response["content"] = _error_content()
    operation["responses"] = dict(sorted(responses.items(), key=_status_order))


def _status_order(item: tuple[str, Any]) -> tuple[int, str]:
    status = item[0]
    return (1000, status) if not status.isdigit() else (int(status), status)


def _drop_unused_validation_schemas(schema: dict[str, Any], components: dict[str, Any]) -> None:
    # порядок важен: ValidationError упоминается только из HTTPValidationError
    for name in _FASTAPI_VALIDATION_SCHEMAS:
        if name in components and f'"{REF_PREFIX}{name}"' not in json.dumps(schema):
            del components[name]


# --------------------------------------------------------------------------------------
# Сборка
# --------------------------------------------------------------------------------------


def postprocess_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Дополняет схему (см. docstring модуля). Идемпотентна: повторный вызов ничего не
    меняет."""
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    documented = _documented_responses()
    models: list[tuple[type[BaseModel], JsonSchemaMode]] = [(ErrorEnvelope, "serialization")]
    models += list(_REGISTERED.values())
    models += [(_spec_model(d.spec), "serialization") for d in documented.values()]
    definitions, refs, conflicts = collect_definitions(dict.fromkeys(models))
    if conflicts:
        logger.warning("OpenAPI: одноимённые модели с разными схемами: %s", conflicts)
    for name, body in definitions.items():
        # схема, которую уже построил FastAPI (та же модель из response_model), главнее
        components.setdefault(name, body)
    envelope = components[ErrorEnvelope.__name__]
    envelope["description"] = (
        "Единый конверт ошибки API (api-contract.md §1.4): любой ответ 4xx/5xx приходит в "
        "этой форме. Коды `error.code`:\n\n" + error_codes_markdown()
    )

    for path, item in (schema.get("paths") or {}).items():
        if not path.startswith(API_PREFIX):
            continue
        for method, operation in item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            _add_standard_responses(path, method, operation)
            spec = documented.get((method, path))
            if spec is not None:
                _document_success(operation["responses"], spec, refs, components)
    _drop_unused_validation_schemas(schema, components)
    return schema


def install_openapi_postprocessing(app: FastAPI) -> None:
    """Оборачивает `app.openapi` (после остальных обёрток): схема строится как раньше и
    дополняется. Результат кэшируется вместе со схемой FastAPI."""
    original = app.openapi
    processed: list[dict[str, Any]] = []

    def openapi() -> dict[str, Any]:
        schema = original()
        if processed and processed[0] is schema:
            return schema
        processed[:] = [postprocess_openapi(schema)]
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
