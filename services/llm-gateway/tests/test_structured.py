"""Structured output (бета): response_format уходит; 400/422 → повтор без него, память флага."""
from __future__ import annotations

import httpx
import pytest

from tests.conftest import FakeGigaChat, chat_ok, chat_payload, status

SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"}},
    "required": ["headline"],
}


async def test_response_format_sent(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(chat_ok('{"headline": "ок"}'))
    response = await client.post(
        "/v1/chat", json=chat_payload(response_schema=SCHEMA), headers=auth
    )
    assert response.status_code == 200
    assert response.json()["structured"] is True
    body = fake.chat_bodies()[0]
    assert body["response_format"] == {"type": "json_schema", "schema": SCHEMA, "strict": False}

    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["structured_supported"] is True


@pytest.mark.parametrize("code", [400, 422])
async def test_rejected_response_format_falls_back_and_is_remembered(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str], code: int
) -> None:
    fake.chat_queue.extend([status(code, message="response_format unsupported"), chat_ok("{}")])
    response = await client.post(
        "/v1/chat", json=chat_payload(response_schema=SCHEMA), headers=auth
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["structured"] is False
    bodies = fake.chat_bodies()
    assert "response_format" in bodies[0]
    assert "response_format" not in bodies[1]

    # следующий вызов со схемой уже не пробует response_format
    response = await client.post(
        "/v1/chat", json=chat_payload(response_schema=SCHEMA), headers=auth
    )
    assert response.status_code == 200
    assert "response_format" not in fake.chat_bodies()[2]
    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["structured_supported"] is False


async def test_400_without_schema_is_upstream_error(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(status(400, message="bad request"))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_upstream_error"
    assert len(fake.chat_requests) == 1
