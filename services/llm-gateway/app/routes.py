"""Маршруты шлюза: /v1/chat (вызов модели), /v1/usage (счётчики), /healthz, /readyz."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.budget import BudgetExceeded, DailyBudget
from app.config import Settings
from app.errors import ApiError, trace_id
from app.gate import ConcurrencyGate, QueueFull, QueueTimeout
from app.metrics import CallStats
from app.providers import Provider, ProviderError, ProviderReply, ProviderRequest
from app.schemas import ChatRequest, ChatResponse, HealthOut, ReadyOut, UsageReportOut
from app.security import require_internal_token

logger = logging.getLogger("app.chat")

router = APIRouter()

# очередь переполнена или слот не дождались — backend повторит не раньше, чем через 30 с
QUEUE_RETRY_AFTER_SECONDS = 30


def input_sha256(messages: list[dict[str, str]]) -> str:
    """Хеш канонического JSON сообщений: в логе видно «тот же вход», но не сам вход."""
    canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _model_name(settings: Settings, provider: Provider, tier: str) -> str:
    return "stub" if provider.name == "stub" else settings.model_for(tier)


@router.post(
    "/v1/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_internal_token)],
    summary="Вызвать модель: сообщения → текст и usage (очередь, лимит, ретраи — внутри)",
)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    state = request.app.state
    settings: Settings = state.settings
    provider: Provider = state.provider
    stats: CallStats = state.stats
    messages = [message.model_dump() for message in payload.messages]
    model = _model_name(settings, provider, payload.tier)
    event: dict[str, Any] = {
        "event": "llm.chat",
        "request_id": trace_id(request),
        "purpose": payload.purpose,
        "tier": payload.tier,
        "model": model,
        "status": "ok",
        "attempts": 0,
        "queue_ms": 0,
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "input_chars": sum(len(message["content"]) for message in messages),
        "input_sha256": input_sha256(messages),
        "structured": False,
    }
    try:
        reply, queue_ms, latency_ms = await _call(
            state, settings, provider, payload, messages, model, event
        )
    except ApiError as exc:
        event["status"] = exc.code
        stats.record_error(exc.code)
        raise
    except Exception:
        event["status"] = "internal_error"
        stats.record_error("internal_error")
        raise
    finally:
        logger.info("llm.chat", extra=event)

    stats.record_ok(latency_ms)
    return ChatResponse(
        text=reply.text,
        model=reply.model,
        finish_reason=reply.finish_reason,
        usage={
            "prompt_tokens": reply.prompt_tokens,
            "completion_tokens": reply.completion_tokens,
            "total_tokens": reply.total_tokens,
        },
        structured=reply.structured,
        latency_ms=latency_ms,
        queue_ms=queue_ms,
        attempts=reply.attempts,
    )


def _budget_error(exc: BudgetExceeded, cap: int) -> ApiError:
    return ApiError(
        503,
        "llm_budget_exceeded",
        f"Дневной лимит GigaChat ({cap} токенов) исчерпан; сброс в 00:00 МСК",
        headers={"Retry-After": str(exc.retry_after)},
    )


async def _call(
    state: Any,
    settings: Settings,
    provider: Provider,
    payload: ChatRequest,
    messages: list[dict[str, str]],
    model: str,
    event: dict[str, Any],
) -> tuple[ProviderReply, int, int]:
    input_chars = event["input_chars"]
    if input_chars > settings.llm_max_input_chars:
        raise ApiError(
            400,
            "validation_error",
            f"Суммарная длина сообщений {input_chars} символов больше лимита "
            f"{settings.llm_max_input_chars} (LLM_MAX_INPUT_CHARS)",
        )
    if payload.max_tokens > settings.llm_max_output_tokens:
        raise ApiError(
            400,
            "validation_error",
            f"max_tokens={payload.max_tokens} больше лимита {settings.llm_max_output_tokens} "
            "(LLM_MAX_OUTPUT_TOKENS)",
        )
    if not provider.configured:
        raise ApiError(
            503,
            "llm_not_configured",
            "GigaChat не настроен на этом стенде: пустой GIGACHAT_CREDENTIALS",
        )

    budget: DailyBudget = state.budget
    gate: ConcurrencyGate = state.gate
    try:
        budget.check()
        async with gate.slot() as queue_ms:
            event["queue_ms"] = queue_ms
            # пока ждали слот, лимит могли выбрать другие запросы
            budget.check()
            started = time.monotonic()
            reply = await provider.chat(
                ProviderRequest(
                    messages=messages,
                    model=model,
                    temperature=payload.temperature,
                    max_tokens=payload.max_tokens,
                    response_schema=payload.response_schema,
                    request_id=event["request_id"],
                )
            )
            latency_ms = int((time.monotonic() - started) * 1000)
    except BudgetExceeded as exc:
        raise _budget_error(exc, settings.llm_daily_token_cap) from None
    except QueueFull:
        raise ApiError(
            429,
            "queue_full",
            f"Очередь к GigaChat заполнена ({gate.queue_max} ждущих): "
            "он обрабатывает запросы по одному",
            headers={"Retry-After": str(QUEUE_RETRY_AFTER_SECONDS)},
        ) from None
    except QueueTimeout:
        raise ApiError(
            503,
            "queue_timeout",
            f"Очередь к GigaChat не продвинулась за {gate.queue_timeout:g} с",
            headers={"Retry-After": str(QUEUE_RETRY_AFTER_SECONDS)},
        ) from None
    except ProviderError as exc:
        budget.add(exc.spent_tokens)
        event["attempts"] = exc.attempts
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        raise ApiError(exc.http_status, exc.code, exc.message, headers=headers) from None

    budget.add(reply.spent_tokens)
    event.update(
        attempts=reply.attempts,
        latency_ms=latency_ms,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
        structured=reply.structured,
        model=reply.model,
    )
    return reply, event["queue_ms"], latency_ms


@router.get(
    "/v1/usage",
    response_model=UsageReportOut,
    dependencies=[Depends(require_internal_token)],
    summary="Счётчики шлюза: токены за сутки МСК, очередь, исходы, задержки",
)
async def usage(request: Request) -> UsageReportOut:
    state = request.app.state
    settings: Settings = state.settings
    provider: Provider = state.provider
    budget: DailyBudget = state.budget
    stats: CallStats = state.stats
    return UsageReportOut(
        provider=provider.name,
        configured=provider.configured,
        model_strong=settings.gigachat_model_strong,
        model_fast=settings.gigachat_model_fast,
        day=budget.day.isoformat(),
        tokens_today=budget.tokens_today,
        daily_cap=budget.cap,
        gate=state.gate.snapshot(),
        requests={"ok": stats.ok, "error": dict(stats.errors)},
        latency_ms=stats.latency(),
        structured_supported=provider.structured_supported,
        token_cached=provider.token_cached,
    )


@router.get("/healthz", response_model=HealthOut, summary="Liveness: процесс жив")
async def healthz() -> HealthOut:
    return HealthOut()


@router.get(
    "/readyz",
    response_model=ReadyOut,
    summary="Readiness: всегда 200, пока процесс жив; доступность GigaChat не проверяется",
)
async def readyz(request: Request) -> ReadyOut:
    # Под не должен флапать из-за интернета: проблемы GigaChat видны в checks и в
    # ответах /v1/chat, а не в балансировке.
    state = request.app.state
    provider: Provider = state.provider
    return ReadyOut(
        checks={
            "provider": provider.name,
            "configured": provider.configured,
            "ca_bundle": state.tls.ca_bundle,
            "token": "cached" if provider.token_cached else "none",
        }
    )
