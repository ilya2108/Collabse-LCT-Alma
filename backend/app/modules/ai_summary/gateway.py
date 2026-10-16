"""HTTP-клиент llm-gateway (`POST {LLM_GATEWAY_URL}/v1/chat`) и коды ошибок задачи.

Ключ GigaChat backend не видит: он знает только `LLM_GATEWAY_TOKEN` (заголовок
`X-Internal-Token`). Ответы шлюза об ошибках (`{"error": {"code", …}}`) сводятся к кодам
задачи (§2.10 спецификации, поле `error.code` ответа, не HTTP-коды API): `llm_busy`,
`llm_timeout`, `llm_unavailable`, `llm_not_configured`, `llm_budget_exceeded`.
В логи не попадают ни сообщения, ни ответ модели, ни токен.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.ai_summary.gateway")

# код задачи → сообщение для интерфейса (нейтральное, по-русски)
ERROR_MESSAGES: dict[str, str] = {
    "llm_unavailable": "GigaChat сейчас недоступен",
    "llm_busy": "GigaChat занят другими сводками — он обрабатывает по одной",
    "llm_timeout": "GigaChat не ответил вовремя",
    "llm_not_configured": "GigaChat не настроен на этом стенде",
    "llm_budget_exceeded": "Дневной лимит GigaChat исчерпан",
    "llm_bad_output": "Ответ GigaChat не прошёл проверку цифр",
    "interrupted": "Формирование прервано",
    "rate_limited": "Лимит сводок GigaChat на пользователя за час исчерпан",
    "disabled": "Сводки ИИ выключены администратором",
    "internal": "Не удалось сформировать сводку",
}

# код ошибки шлюза → код задачи
_GATEWAY_CODES: dict[str, str] = {
    "queue_full": "llm_busy",
    "queue_timeout": "llm_busy",
    "llm_rate_limited": "llm_busy",
    "llm_not_configured": "llm_not_configured",
    "llm_budget_exceeded": "llm_budget_exceeded",
    "llm_timeout": "llm_timeout",
    "llm_auth_failed": "llm_unavailable",
    "llm_upstream_error": "llm_unavailable",
}


def error_message(code: str) -> str:
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES["internal"])


class ProviderError(Exception):
    """Сбой провайдера сводки: `code` — код задачи (§2.10)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code if code in ERROR_MESSAGES else "internal"
        self.message = message or error_message(self.code)
        super().__init__(self.message)


@dataclass(frozen=True)
class GatewayReply:
    text: str
    model: str | None
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    structured: bool
    latency_ms: int
    queue_ms: int
    attempts: int


def map_gateway_error(status: int, code: str | None) -> str:
    """HTTP-статус и код ошибки шлюза → код задачи."""
    if code in _GATEWAY_CODES:
        return _GATEWAY_CODES[code]
    if status == 401:
        return "llm_unavailable"
    if status == 429:
        return "llm_busy"
    if status == 504:
        return "llm_timeout"
    if status >= 500:
        return "llm_unavailable"
    return "internal"


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class GatewayClient:
    """Клиент шлюза; `transport` подменяют тесты (`httpx.MockTransport`)."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.llm_gateway_url).rstrip("/")
        self.token = settings.llm_gateway_token if token is None else token
        self.timeout = timeout or settings.ai_summary_gateway_timeout_seconds
        self.transport = transport

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str,
        request_id: str,
        response_schema: dict[str, Any] | None = None,
        tier: str = "strong",
        temperature: float = 0.2,
        max_tokens: int = 1500,
    ) -> GatewayReply:
        body: dict[str, Any] = {
            "messages": messages,
            "tier": tier,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "purpose": purpose,
        }
        if response_schema is not None:
            body["response_schema"] = response_schema
        headers = {"X-Internal-Token": self.token, "X-Request-Id": request_id}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                transport=self.transport,
            ) as client:
                response = await client.post("/v1/chat", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError("llm_timeout") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "ai_summary.gateway_unreachable request_id=%s error=%s",
                request_id,
                type(exc).__name__,
            )
            raise ProviderError("llm_unavailable") from exc
        if response.status_code != 200:
            gateway_code = _error_code(response)
            if response.status_code == 401:
                logger.error(
                    "ai_summary.gateway_unauthorized request_id=%s: неверный LLM_GATEWAY_TOKEN",
                    request_id,
                )
            code = map_gateway_error(response.status_code, gateway_code)
            logger.warning(
                "ai_summary.gateway_error request_id=%s status=%s gateway_code=%s code=%s",
                request_id,
                response.status_code,
                gateway_code,
                code,
            )
            raise ProviderError(code)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("llm_unavailable") from exc
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise ProviderError("llm_unavailable")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return GatewayReply(
            text=data["text"],
            model=data.get("model") if isinstance(data.get("model"), str) else None,
            finish_reason=data.get("finish_reason"),
            prompt_tokens=_int(usage.get("prompt_tokens")),
            completion_tokens=_int(usage.get("completion_tokens")),
            structured=bool(data.get("structured")),
            latency_ms=_int(data.get("latency_ms")) or int((time.monotonic() - started) * 1000),
            queue_ms=_int(data.get("queue_ms")),
            attempts=_int(data.get("attempts")) or 1,
        )
