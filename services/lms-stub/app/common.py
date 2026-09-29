"""Мелкие помощники сервиса: время, JSON, единый формат ошибок."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse


def now_iso() -> str:
    """Текущее время UTC в ISO-8601 с суффиксом Z — как в конверте IntegrationEvent."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_json(data: Any) -> str:
    """Компактный JSON без экранирования кириллицы.

    Именно эта строка (в UTF-8) уходит в HTTP-тело и подписывается HMAC,
    поэтому сериализация выполняется ровно один раз — при постановке в outbox.
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def pretty_json(raw: str) -> str:
    """Отформатированный JSON для журнала; невалидный текст возвращается как есть."""
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return raw


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    """Ошибка в едином формате CRM (api-contract §1.4): code машинный, message на русском."""
    error: dict[str, Any] = {"code": code, "message": message, "trace_id": str(uuid.uuid4())}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})
