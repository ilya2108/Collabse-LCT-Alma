"""Схемы внутреннего API шлюза (контракт backend ↔ llm-gateway, README §API)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=8)
    tier: Literal["strong", "fast"] = "strong"
    # API GigaChat отвергает 0 — шлюз поднимает до 0.01 сам
    temperature: float = Field(0.2, ge=0, le=1)
    # верхняя граница — LLM_MAX_OUTPUT_TOKENS (проверяется в маршруте: она из настроек)
    max_tokens: int = Field(1500, ge=16)
    # необязательная JSON Schema ответа: уходит в GigaChat как response_format (бета)
    response_schema: dict[str, Any] | None = None
    # только для логов и метрик
    purpose: str = Field("unspecified", max_length=40, pattern=r"^[a-z0-9_]+$")


class UsageOut(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    text: str
    model: str
    finish_reason: str | None = None
    usage: UsageOut
    # True — ответ получен с response_format (structured output); False — по инструкции промпта
    structured: bool
    latency_ms: int
    queue_ms: int
    attempts: int


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyChecks(BaseModel):
    provider: str
    configured: bool
    # loaded | missing | invalid | not_set
    ca_bundle: str
    # cached | none
    token: str


class ReadyOut(BaseModel):
    status: Literal["ok"] = "ok"
    checks: ReadyChecks


class GateOut(BaseModel):
    limit: int
    in_flight: int
    waiting: int
    total: int
    max_in_flight: int


class RequestsOut(BaseModel):
    ok: int
    error: dict[str, int]


class LatencyOut(BaseModel):
    p50: int | None = None
    p95: int | None = None
    last: int | None = None


class UsageReportOut(BaseModel):
    provider: str
    configured: bool
    model_strong: str
    model_fast: str
    day: str
    tokens_today: int
    daily_cap: int
    gate: GateOut
    requests: RequestsOut
    latency_ms: LatencyOut
    structured_supported: bool | None
    token_cached: bool
