"""X-Internal-Token обязателен для /v1/*; health-пробы — без токена."""
from __future__ import annotations

import httpx
import pytest

from tests.conftest import FakeGigaChat, chat_payload


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}])
async def test_chat_requires_token(
    client: httpx.AsyncClient, fake: FakeGigaChat, headers: dict[str, str]
) -> None:
    response = await client.post("/v1/chat", json=chat_payload(), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert fake.chat_requests == [] and fake.oauth_requests == []


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}])
async def test_usage_requires_token(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.get("/v1/usage", headers=headers)
    assert response.status_code == 401


async def test_token_prefix_or_longer_value_is_rejected(client: httpx.AsyncClient) -> None:
    for value in ("test-llm", "test-llm-token-and-more", "TEST-LLM-TOKEN"):
        response = await client.get("/v1/usage", headers={"X-Internal-Token": value})
        assert response.status_code == 401


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_health_without_token(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200


async def test_usage_shape(client: httpx.AsyncClient, auth: dict[str, str]) -> None:
    response = await client.get("/v1/usage", headers=auth)
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "gigachat"
    assert data["configured"] is True
    assert data["model_strong"] == "GigaChat-2-Pro"
    assert data["model_fast"] == "GigaChat-2"
    assert set(data["gate"]) == {"limit", "in_flight", "waiting", "total", "max_in_flight"}
    assert data["requests"]["ok"] == 0
    assert data["requests"]["error"]["queue_full"] == 0
    assert data["latency_ms"] == {"p50": None, "p95": None, "last": None}
    assert data["structured_supported"] is None
    assert data["token_cached"] is False


# APP_ENV=k8s/prod с dev-токеном шлюз не стартует вовсе (test_config) — здесь dev и test
@pytest.mark.parametrize("app_env", ["dev", "test"])
@pytest.mark.parametrize("token", ["dev-llm-token", ""])
async def test_dev_token_refused_with_real_key(
    build, fake: FakeGigaChat, app_env: str, token: str
) -> None:
    """Настоящий ключ + общеизвестный dev-токен: отказ при любом APP_ENV (dev-стенд в LAN)."""
    client, _ = await build(app_env=app_env, llm_gateway_token=token)
    response = await client.post(
        "/v1/chat", json=chat_payload(), headers={"X-Internal-Token": "dev-llm-token"}
    )
    assert response.status_code == 401
    assert "dev-токен" in response.json()["error"]["message"]
    assert fake.chat_requests == [] and fake.oauth_requests == []


async def test_dev_token_allowed_without_key_or_with_stub(build, fake: FakeGigaChat) -> None:
    """Без ключа (503 llm_not_configured) и в stub dev-токен по-прежнему работает."""
    headers = {"X-Internal-Token": "dev-llm-token"}
    no_key, _ = await build(llm_gateway_token="dev-llm-token", gigachat_credentials="")
    response = await no_key.post("/v1/chat", json=chat_payload(), headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
    stub, _ = await build(llm_gateway_token="dev-llm-token", llm_provider="stub")
    assert (await stub.post("/v1/chat", json=chat_payload(), headers=headers)).status_code == 200
