"""Единый формат ошибок API и обработчики исключений.

Контракт — docs/design/api-contract.md §1.4:
`{"error": {"code", "message", "details", "trace_id"}}`; `message` — человекочитаемый,
на русском; `code` — стабильный машинный; `trace_id` = X-Request-Id.

`ERROR_CODES` — каталог машинных кодов, которые возвращает сервер; модели
`ErrorEnvelope`/`ErrorBody`/`ErrorDetail` описывают тело ошибки в Swagger
(app.core.openapi). Каталог сверяется тестом tests/test_error_catalog.py с таблицей
§1.4 и с кодами, которые реально бросает код (`ApiError`, ответы middleware).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-Id"

# trace_id текущего запроса; проставляется middleware, читается обработчиками и логами
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# Каталог машинных кодов (api-contract.md §1.4): код → (HTTP-статус, когда возникает).
# Только коды, которые сервер действительно возвращает; новый код — сюда, в таблицу §1.4
# и в код одновременно (иначе упадёт tests/test_error_catalog.py). Swagger показывает
# каталог в описании схемы ErrorBody.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "validation_error": (
        400,
        "Ошибка валидации тела или параметров запроса; в details — поле и причина",
    ),
    "import_bad_format": (400, "Файл импорта не распознан (не XLSX/XLS/CSV/JSON)"),
    "import_encoding_error": (400, "Не удалось определить кодировку файла импорта"),
    "workflow_invalid_schema": (400, "Схема процесса после операции невалидна"),
    "idempotency_key_invalid": (400, "Заголовок Idempotency-Key не по формату"),
    "unauthorized": (401, "Нет токена, токен просрочен или неверна подпись вебхука"),
    "forbidden": (
        403,
        "Нет права, процесс B2B/B2C недоступен роли, учётная запись деактивирована",
    ),
    "not_found": (404, "Объект не найден или вне области роли (существование не раскрываем)"),
    "method_not_allowed": (405, "HTTP-метод не поддерживается для этого пути"),
    "conflict": (409, "Нарушение уникальности или состояние объекта не допускает операцию"),
    "stale_version": (409, "Объект изменён другим пользователем: version устарел"),
    "workflow_invalid_transition": (409, "Переход не разрешён схемой процесса"),
    "workflow_busy": (409, "Схема процесса сейчас применяется; повтор по Retry-After"),
    "idempotency_in_progress": (409, "Запрос с этим Idempotency-Key ещё выполняется"),
    "idempotency_replay_unavailable": (
        409,
        "Операция по ключу выполнена, сохранённый ответ недоступен — перечитать данные",
    ),
    "role_exists": (409, "Код или название роли уже заняты"),
    "role_in_use": (409, "Роль назначена пользователям или указана в переходах процессов"),
    "role_locked": (409, "Права и область роли admin не редактируются"),
    "role_system": (409, "Системную роль нельзя удалить"),
    "access_lockout": (409, "Изменение оставляет систему без управления ролями"),
    "file_too_large": (413, "Файл или тело запроса больше допустимого размера"),
    "unprocessable": (422, "Запрос корректен по форме, но не выполним по смыслу"),
    "idempotency_key_reused": (422, "Idempotency-Key уже использован для другого запроса"),
    "internal_error": (500, "Необработанная ошибка; детали — только в логах"),
    "integration_unavailable": (502, "Внешняя система (MinIO, Keycloak Admin API) недоступна"),
    "service_unavailable": (
        503,
        "Хранилище ключей идемпотентности или одноразовых кодов недоступно",
    ),
}

# коды для «голых» HTTPException по статусу: фреймворк сам бросает 400 (тело не
# разобрано), 404 (нет маршрута) и 405; остальные статусы — страховка
_STATUS_CODES: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    422: "unprocessable",
    500: "internal_error",
    502: "integration_unavailable",
    503: "service_unavailable",
}

_STATUS_MESSAGES: dict[int, str] = {
    400: "Ошибка валидации данных",
    401: "Требуется аутентификация",
    403: "Недостаточно прав для выполнения операции",
    404: "Объект не найден",
    405: "Метод не поддерживается",
    409: "Конфликт данных",
    413: "Файл превышает допустимый размер",
    422: "Запрос не может быть обработан",
    500: "Внутренняя ошибка сервера",
    502: "Внешний сервис недоступен",
    503: "Сервис временно недоступен",
}


def status_error_code(status_code: int) -> str:
    """Код каталога для HTTP-статуса без своего кода: прочие 5xx — `internal_error`,
    прочие 4xx — `validation_error` (каталог закрыт, лишних кодов клиент не видит)."""
    if status_code in _STATUS_CODES:
        return _STATUS_CODES[status_code]
    return _STATUS_CODES[500] if status_code >= 500 else _STATUS_CODES[400]


# --- тело ошибки для Swagger (форма — error_payload ниже) ----------------------------------


class ErrorDetail(BaseModel):
    """Уточнение ошибки: поле и причина (валидация, строки импорта, права роли)."""

    # у части деталей есть свои ключи: permission/requires у прав роли, row у импорта
    model_config = ConfigDict(extra="allow")

    field: str | None = Field(
        default=None, description="Путь к полю: `email`, `filters.kam_id`, `options.encoding`"
    )
    code: str = Field(description="Уточняющий код: `missing`, `permission_dependency` …")
    message: str = Field(description="Причина по-русски")


class ErrorBody(BaseModel):
    code: str = Field(
        description="Стабильный машинный код из каталога (api-contract.md §1.4)",
        examples=["validation_error"],
    )
    message: str = Field(
        description="Сообщение по-русски — интерфейс показывает его как есть",
        examples=["Ошибка валидации данных"],
    )
    details: list[ErrorDetail] | None = Field(
        default=None, description="Есть у ошибок валидации и построчных ошибок импорта"
    )
    trace_id: str | None = Field(
        description="Совпадает с заголовком X-Request-Id — по нему ищут запрос в логах",
        examples=["6f9a1c2e-6f1b-4e0e-9c1a-2b3c4d5e6f70"],
    )


class ErrorEnvelope(BaseModel):
    """Единый конверт ошибки API: любой ответ 4xx/5xx приходит в этой форме."""

    error: ErrorBody


class ApiError(Exception):
    """Доменная ошибка с телом по контракту §1.4."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


# --- фабрики частых ошибок --------------------------------------------------------------


def unauthorized(message: str = "Требуется аутентификация") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str = "Недостаточно прав для выполнения операции") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Объект не найден") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(message: str = "Конфликт данных") -> ApiError:
    return ApiError(409, "conflict", message)


def stale_version(
    message: str = "Объект был изменён другим пользователем, обновите данные",
) -> ApiError:
    return ApiError(409, "stale_version", message)


def validation_error(
    message: str = "Ошибка валидации данных", details: list[dict[str, Any]] | None = None
) -> ApiError:
    return ApiError(400, "validation_error", message, details)


def unprocessable(message: str = "Запрос не может быть обработан") -> ApiError:
    return ApiError(422, "unprocessable", message)


# --- построение ответа --------------------------------------------------------------------


def error_payload(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "trace_id": request_id_var.get() or None,
        }
    }
    if details:
        body["error"]["details"] = details
    return body


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    all_headers = dict(headers or {})
    rid = request_id_var.get()
    if rid:
        all_headers[REQUEST_ID_HEADER] = rid
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message, details),
        headers=all_headers,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Проставляет X-Request-Id (uuid) в контекст и в ответ (api-contract.md §1.3)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


# --- обработчики -----------------------------------------------------------------------------


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(part) for part in err.get("loc", []) if part != "body"),
            "code": err.get("type", "invalid"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    # контракт: ошибки валидации тела/параметров — 400 validation_error (не 422 FastAPI)
    return _error_response(400, "validation_error", "Ошибка валидации данных", details)


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = status_error_code(exc.status_code)
    message = (
        exc.detail
        if isinstance(exc.detail, str) and exc.detail not in ("", "Not Found")
        else _STATUS_MESSAGES.get(exc.status_code, "Ошибка обработки запроса")
    )
    return _error_response(exc.status_code, code, message, headers=dict(exc.headers or {}))


async def _handle_stale_data(request: Request, exc: StaleDataError) -> JSONResponse:
    return _error_response(
        409, "stale_version", "Объект был изменён другим пользователем, обновите данные"
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # детали — только в логах (контракт §1.4), наружу — стабильный internal_error
    logger.exception("Необработанная ошибка: %s %s", request.method, request.url.path)
    return _error_response(500, "internal_error", "Внутренняя ошибка сервера")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(StaleDataError, _handle_stale_data)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected)
