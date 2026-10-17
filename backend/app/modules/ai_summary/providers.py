"""Провайдеры сводки: GigaChat через llm-gateway и stub без сети.

Провайдер получает готовые сообщения и схему, возвращает текст и usage; промпт, разбор
JSON и проверка чисел — в `service`. Режим «по правилам» провайдером не является: он
считается синхронно в запросе (`rules.rules_sections`).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.modules.ai_summary.gateway import GatewayClient, ProviderError
from app.modules.ai_summary.prompt import MAX_TOKENS, TEMPERATURE, TIER, facts_from_messages
from app.modules.ai_summary.rules import rules_sections


@dataclass(frozen=True)
class LlmReply:
    text: str
    model: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    structured: bool = False


class SummaryProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        purpose: str,
        request_id: str,
    ) -> LlmReply: ...


class GigaChatGatewayProvider:
    """GigaChat через llm-gateway: ключ, OAuth, TLS НУЦ, семафор и лимиты — в шлюзе."""

    name = "gigachat"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = GatewayClient(transport=transport)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        purpose: str,
        request_id: str,
    ) -> LlmReply:
        reply = await self.client.chat(
            messages,
            purpose=purpose,
            request_id=request_id,
            response_schema=schema,
            tier=TIER,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
        return LlmReply(
            text=reply.text,
            model=reply.model,
            tokens_in=reply.prompt_tokens,
            tokens_out=reply.completion_tokens,
            latency_ms=reply.latency_ms,
            structured=reply.structured,
        )


class StubProvider:
    """Детерминированный ответ без сети (dev и тесты; в контуре запрещён настройками):
    сводка по правилам из фактов пользовательского сообщения в форме ответа модели."""

    name = "stub"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        purpose: str,
        request_id: str,
    ) -> LlmReply:
        started = time.monotonic()
        facts = facts_from_messages(messages)
        if facts is None:
            raise ProviderError("internal", "Stub-провайдер не нашёл факты в сообщении")
        text = json.dumps(rules_sections(facts), ensure_ascii=False)
        return LlmReply(
            text=text,
            model="stub",
            tokens_in=0,
            tokens_out=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            structured=True,
        )


def get_provider(name: str) -> SummaryProvider:
    """Провайдер задачи по имени (`gigachat` | `stub`)."""
    if name == "gigachat":
        return GigaChatGatewayProvider()
    if name == "stub":
        return StubProvider()
    raise ProviderError("internal", f"Неизвестный провайдер сводки: {name}")
