"""POST /v1/chat: тело запроса к GigaChat, разбор ответа, 401 → переавторизация, валидация."""
from __future__ import annotations

import httpx
import pytest

from tests.conftest import FakeGigaChat, chat_ok, chat_payload, status


@pytest.mark.parametrize(
    ("tier", "model"), [("strong", "GigaChat-2-Pro"), ("fast", "GigaChat-2")]
)
async def test_request_body_and_headers(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str], tier: str, model: str
) -> None:
    response = await client.post(
        "/v1/chat",
        json=chat_payload(tier=tier, temperature=0, max_tokens=777),
        headers={**auth, "X-Request-Id": "req-123"},
    )
    assert response.status_code == 200, response.text

    request = fake.chat_requests[0]
    assert str(request.url) == "https://giga.test/api/v1/chat/completions"
    assert request.headers["X-Request-ID"] == "req-123"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "alma-llm-gateway/1.0"
    body = fake.chat_bodies()[0]
    assert body == {
        "model": model,
        "messages": chat_payload()["messages"],
        "temperature": 0.01,  # API отвергает 0
        "max_tokens": 777,
        "stream": False,
    }


async def test_response_parsed(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(chat_ok("Привет", model="GigaChat-2-Pro:2.0", usage=(120, 30, 150)))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Привет"
    assert data["model"] == "GigaChat-2-Pro:2.0"
    assert data["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    assert data["structured"] is False
    assert data["attempts"] == 1
    assert isinstance(data["latency_ms"], int) and isinstance(data["queue_ms"], int)


async def test_empty_choices_is_upstream_error(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(httpx.Response(200, json={"choices": [], "model": "GigaChat-2-Pro"}))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_upstream_error"


async def test_chat_401_reauthorizes_once_and_repeats(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.extend([status(401), chat_ok()])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200, response.text
    assert len(fake.oauth_requests) == 2
    assert len(fake.chat_requests) == 2


async def test_chat_401_twice_is_auth_failed(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.extend([status(401), status(401)])
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_auth_failed"
    assert len(fake.chat_requests) == 2


async def test_other_4xx_is_upstream_error_without_retry(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(status(404, message="No such model"))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "llm_upstream_error"
    assert "404" in error["message"]
    assert len(fake.chat_requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        chat_payload(messages=[]),
        chat_payload(messages=[{"role": "user", "content": "x"}] * 9),
        chat_payload(messages=[{"role": "tool", "content": "x"}]),
        chat_payload(tier="huge"),
        chat_payload(temperature=1.5),
        chat_payload(max_tokens=8),
        chat_payload(purpose="Не латиница"),
        chat_payload(purpose="x" * 41),
    ],
)
async def test_invalid_body_is_400(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str], payload: dict
) -> None:
    response = await client.post(
        "/v1/chat", json=payload, headers={**auth, "X-Request-Id": "trace-1"}
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["trace_id"] == "trace-1"
    assert fake.chat_requests == []


async def test_input_and_output_limits(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(llm_max_input_chars=20, llm_max_output_tokens=500)
    long_input = chat_payload(messages=[{"role": "user", "content": "я" * 21}])
    response = await client.post("/v1/chat", json=long_input, headers=auth)
    assert response.status_code == 400
    assert "LLM_MAX_INPUT_CHARS" in response.json()["error"]["message"]

    too_many_tokens = chat_payload(messages=[{"role": "user", "content": "ок"}], max_tokens=501)
    response = await client.post("/v1/chat", json=too_many_tokens, headers=auth)
    assert response.status_code == 400
    assert "LLM_MAX_OUTPUT_TOKENS" in response.json()["error"]["message"]
    assert fake.chat_requests == []


async def test_trace_id_generated_when_absent(
    client: httpx.AsyncClient, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    fake.chat_queue.append(status(404))
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    trace = response.json()["error"]["trace_id"]
    assert len(trace) == 36
    # тот же id ушёл в GigaChat как X-Request-ID
    assert fake.chat_requests[0].headers["X-Request-ID"] == trace
