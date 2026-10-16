"""LLM_PROVIDER=stub: детерминированный ответ без сети, ключ не нужен."""
from __future__ import annotations

import json

from tests.conftest import FakeGigaChat, chat_payload


async def test_stub_with_schema(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_provider="stub", gigachat_credentials="")
    response = await client.post(
        "/v1/chat",
        json=chat_payload(response_schema={"type": "object"}),
        headers=auth,
    )
    assert response.status_code == 200
    data = response.json()
    assert json.loads(data["text"]) == {
        "headline": "Тестовый ответ llm-gateway (LLM_PROVIDER=stub).",
        "going_well": [],
        "lagging": [],
        "attention": [],
        "questions": [],
    }
    assert data["model"] == "stub"
    assert data["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert data["structured"] is True
    assert fake.oauth_requests == [] and fake.chat_requests == []


async def test_stub_without_schema(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_provider="stub", gigachat_credentials="")
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200
    assert response.json()["text"] == "ОК"

    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["provider"] == "stub"
    assert usage["configured"] is True
    assert usage["tokens_today"] == 0
    readiness = (await client.get("/readyz")).json()["checks"]
    assert readiness["provider"] == "stub" and readiness["configured"] is True
