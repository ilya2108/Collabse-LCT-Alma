"""Единый формат ошибок API и обработчики исключений.

Контракт — docs/design/api-contract.md §1.4:
`{"error": {"code", "message", "details", "trace_id"}}`; `message` — человекочитаемый,
на русском; `code` — стабильный машинный; `trace_id` = X-Request-Id.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-Id"

# trace_id текущего запроса; проставляется middleware, читается обработчиками и логами
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# стабильные машинные коды для «голых» HTTPException по статусу (api-contract.md §1.4)
_STATUS_CODES: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal_error",
    501: "not_implemented",
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
    429: "Слишком много запросов, повторите позже",
    500: "Внутренняя ошибка сервера",
    501: "Метод ещё не реализован",
    502: "Внешний сервис недоступен",
    503: "Сервис временно недоступен",
}


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


def stale_version(message: str = "Объект был изменён другим пользователем, обновите данные") -> (
    ApiError
):
    return ApiError(409, "stale_version", message)


def validation_error(
    message: str = "Ошибка валидации данных", details: list[dict[str, Any]] | None = None
) -> ApiError:
    return ApiError(400, "validation_error", message, details)


def unprocessable(message: str = "Запрос не может быть обработан") -> ApiError:
    return ApiError(422, "unprocessable", message)


def not_implemented(
    message: str = "Функциональность в разработке и появится в следующей стадии",
) -> ApiError:
    return ApiError(501, "not_implemented", message)


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


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
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


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "http_error")
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
