"""Клиент llm-gateway: заголовки и тело запроса, разбор ответа, маппинг ошибок в коды задачи."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.modules.ai_summary.facts import build_team_facts
from app.modules.ai_summary.gateway import (
    ERROR_MESSAGES,
    GatewayClient,
    ProviderError,
    map_gateway_error,
)
from app.modules.ai_summary.prompt import build_messages
from app.modules.ai_summary.providers import GigaChatGatewayProvider, StubProvider, get_provider
from app.modules.ai_summary.rules import rules_sections

from . import ai_summary_samples as samples

MESSAGES = [{"role": "system", "content": "секрет промпта"}, {"role": "user", "content": "x"}]
OK_BODY = {
    "text": '{"headline": "Итог."}',
    "model": "GigaChat-2-Pro",
    "finish_reason": "stop",
    "usage": {"prompt_tokens": 2310, "completion_tokens": 412, "total_tokens": 2722},
    "structured": True,
    "latency_ms": 63120,
    "queue_ms": 5,
    "attempts": 1,
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GatewayClient:
    return GatewayClient(
        base_url="http://llm-gateway:8040",
        token="gw-token",
        timeout=5,
        transport=httpx.MockTransport(handler),
    )


def _error(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"code": code, "message": "…", "trace_id": "t"}},
        headers=headers,
    )


async def test_request_headers_and_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    reply = await _client(handler).chat(
        MESSAGES,
        purpose="kam_summary_team",
        request_id="job-1",
        response_schema={"type": "object"},
        max_tokens=1500,
    )
    (request,) = seen
    assert str(request.url) == "http://llm-gateway:8040/v1/chat"
    assert request.headers["X-Internal-Token"] == "gw-token"
    assert request.headers["X-Request-Id"] == "job-1"
    body = json.loads(request.content)
    assert body == {
        "messages": MESSAGES,
        "tier": "strong",
        "temperature": 0.2,
        "max_tokens": 1500,
        "purpose": "kam_summary_team",
        "response_schema": {"type": "object"},
    }
    assert reply.text == OK_BODY["text"] and reply.model == "GigaChat-2-Pro"
    assert (reply.prompt_tokens, reply.completion_tokens) == (2310, 412)
    assert reply.structured and reply.latency_ms == 63120 and reply.queue_ms == 5


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_error(429, "queue_full", {"Retry-After": "30"}), "llm_busy"),
        (_error(503, "queue_timeout"), "llm_busy"),
        (_error(503, "llm_rate_limited"), "llm_busy"),
        (_error(503, "llm_not_configured"), "llm_not_configured"),
        (_error(503, "llm_budget_exceeded"), "llm_budget_exceeded"),
        (_error(504, "llm_timeout"), "llm_timeout"),
        (_error(502, "llm_auth_failed"), "llm_unavailable"),
        (_error(502, "llm_upstream_error"), "llm_unavailable"),
        (_error(401, "unauthorized"), "llm_unavailable"),
        (_error(400, "validation_error"), "internal"),
        (httpx.Response(500, text="oops"), "llm_unavailable"),
        (httpx.Response(200, text="не json"), "llm_unavailable"),
        (httpx.Response(200, json={"model": "x"}), "llm_unavailable"),
    ],
)
async def test_error_mapping(response: httpx.Response, code: str) -> None:
    with pytest.raises(ProviderError) as exc:
        await _client(lambda _request: response).chat(MESSAGES, purpose="p", request_id="r")
    assert exc.value.code == code
    assert exc.value.message == ERROR_MESSAGES[code]


async def test_connect_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderError) as exc:
        await _client(handler).chat(MESSAGES, purpose="p", request_id="r")
    assert exc.value.code == "llm_unavailable"


async def test_client_timeout_is_llm_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError) as exc:
        await _client(handler).chat(MESSAGES, purpose="p", request_id="r")
    assert exc.value.code == "llm_timeout"


async def test_unauthorized_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.ai_summary")
    with pytest.raises(ProviderError):
        await _client(lambda _request: _error(401, "unauthorized")).chat(
            MESSAGES, purpose="p", request_id="r"
        )
    assert "неверный LLM_GATEWAY_TOKEN" in caplog.text
    assert "gw-token" not in caplog.text and "секрет промпта" not in caplog.text


def test_map_gateway_error_fallbacks() -> None:
    assert map_gateway_error(429, None) == "llm_busy"
    assert map_gateway_error(504, None) == "llm_timeout"
    assert map_gateway_error(503, "unknown") == "llm_unavailable"
    assert map_gateway_error(418, None) == "internal"
    assert ProviderError("nonsense").code == "internal"


async def test_gigachat_provider_passes_schema_and_tier() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=OK_BODY)

    provider = GigaChatGatewayProvider(transport=httpx.MockTransport(handler))
    reply = await provider.complete(
        MESSAGES, schema={"type": "object"}, purpose="kam_summary_kam", request_id="j"
    )
    assert seen[0]["response_schema"] == {"type": "object"}
    assert seen[0]["tier"] == "strong" and seen[0]["temperature"] == 0.2
    assert (reply.tokens_in, reply.tokens_out, reply.model) == (2310, 412, "GigaChat-2-Pro")


async def test_stub_provider_answers_with_rules_from_facts() -> None:
    facts = build_team_facts(samples.team_summary(), samples.team_sla()).facts
    reply = await StubProvider().complete(
        build_messages("team", facts), schema={}, purpose="p", request_id="r"
    )
    assert json.loads(reply.text) == rules_sections(facts)
    assert reply.model == "stub" and reply.tokens_in == 0
    assert isinstance(get_provider("stub"), StubProvider)
    assert isinstance(get_provider("gigachat"), GigaChatGatewayProvider)
    with pytest.raises(ProviderError):
        get_provider("rules")


@pytest.mark.parametrize(
    ("env", "error"),
    [
        ({"ai_summary_provider": "stub"}, "AI_SUMMARY_PROVIDER=stub"),
        ({"ai_summary_provider": "gigachat"}, "LLM_GATEWAY_TOKEN"),
        ({"ai_summary_provider": "gigachat", "llm_gateway_token": " "}, "LLM_GATEWAY_TOKEN"),
    ],
)
def test_settings_fail_closed_in_contour(env: dict[str, str], error: str) -> None:
    base = {
        "app_env": "k8s",
        "internal_api_token": "real-internal",
        "lms_webhook_secret": "real-lms",
        "cms_webhook_secret": "real-cms",
    }
    with pytest.raises(ValueError, match=error):
        Settings(**base, **env)
    # rules и gigachat с настоящим токеном — допустимы
    Settings(**base, ai_summary_provider="rules")
    Settings(**base, ai_summary_provider="gigachat", llm_gateway_token="real-token")
    # вне контура stub разрешён
    assert Settings(app_env="dev", ai_summary_provider="stub").ai_summary_provider == "stub"


def test_settings_error_does_not_echo_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """hide_input_in_errors: ошибка валидатора не печатает в лог пода значения из env."""
    secret_token = "llm-token-tail-must-not-leak-7f3a"
    internal = "internal-token-must-not-leak-91c2"
    monkeypatch.setenv("APP_ENV", "k8s")
    monkeypatch.setenv("AI_SUMMARY_PROVIDER", "stub")  # запрещён в контуре → ошибка
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret_token)
    monkeypatch.setenv("INTERNAL_API_TOKEN", internal)
    monkeypatch.setenv("LMS_WEBHOOK_SECRET", "real-lms")
    monkeypatch.setenv("CMS_WEBHOOK_SECRET", "real-cms")
    with pytest.raises(ValueError) as caught:
        Settings()
    text = str(caught.value)
    assert "AI_SUMMARY_PROVIDER=stub" in text
    for value in (secret_token, secret_token[-8:], internal, "input_value"):
        assert value not in text


async def test_runner_shutdown_marks_cancelled_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Остановка пода: незавершённые задачи отменяются, их строки — сразу interrupted."""
    import asyncio
    import uuid

    from app.modules.ai_summary import runner, service

    started = asyncio.Event()

    async def hang(job_id: uuid.UUID, **_: object) -> None:
        started.set()
        await asyncio.sleep(3600)

    marked: list[list[uuid.UUID]] = []

    async def mark(job_ids: list[uuid.UUID], **_: object) -> int:
        marked.append(list(job_ids))
        return len(job_ids)

    monkeypatch.setattr(service, "run_job", hang)
    monkeypatch.setattr(service, "mark_interrupted", mark)
    job_id = uuid.uuid4()
    runner.spawn(job_id)
    await started.wait()
    assert runner.pending() == 1
    await runner.shutdown()
    assert marked == [[job_id]] and runner.pending() == 0
    # без задач — к БД не обращается
    await runner.shutdown()
    assert marked == [[job_id]]
