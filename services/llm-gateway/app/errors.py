"""Единый формат ошибок API (docs/design/api-contract.md §1.4), как в notification."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Ошибка HTTP API: сериализуется в конверт {"error": {...}}; `headers` — Retry-After и т. п."""

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


def trace_id(request: Request) -> str:
    """X-Request-Id вызывающего или новый uuid4 (одно значение на запрос)."""
    cached = getattr(request.state, "trace_id", None)
    if cached:
        return cached
    value = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.trace_id = value
    return value


def _envelope(
    request: Request, code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "trace_id": trace_id(request)}
    if details:
        error["details"] = details
    return {"error": error}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(request, exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # input (значение поля) в ответ не копируем: это могли быть тексты сообщений
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", []) if part != "body"),
                "code": err.get("type", "invalid"),
                "message": err.get("msg", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content=_envelope(request, "validation_error", "Ошибка валидации данных", details),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "необработанная ошибка llm-gateway",
            extra={"error_type": type(exc).__name__, "request_id": trace_id(request)},
        )
        return JSONResponse(
            status_code=500,
            content=_envelope(request, "internal_error", "Внутренняя ошибка llm-gateway"),
        )
