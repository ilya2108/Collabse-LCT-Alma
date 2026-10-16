"""Живой GigaChat (только вручную): ALMA_LLM_LIVE=1 + GIGACHAT_* в окружении. В CI не идёт.

    set -a; . ../../deploy/k8s/overlays/k3d/secrets/crm-llm.env; set +a
    ALMA_LLM_LIVE=1 GIGACHAT_CA_BUNDLE=$PWD/../../deploy/k8s/base/llm-gateway/certs/\
russian_trusted_bundle.pem uv run pytest -q tests/test_live.py
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("ALMA_LLM_LIVE") != "1", reason="живой GigaChat: ALMA_LLM_LIVE=1"
)

SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "facts": {"type": "array"}},
    "required": ["headline", "facts"],
}


@pytest.fixture()
async def live_client():
    settings = Settings(app_env="test", llm_gateway_token="live-test-token")
    if not settings.credentials:
        pytest.skip("GIGACHAT_CREDENTIALS не задан")
    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://llm-gateway", timeout=300
    ) as client:
        yield client
    await app.state.http.aclose()


async def test_live_fast_and_structured(live_client: httpx.AsyncClient) -> None:
    headers = {"X-Internal-Token": "live-test-token"}
    response = await live_client.post(
        "/v1/chat",
        json={
            "messages": [{"role": "user", "content": "Ответь одним словом: ОК"}],
            "tier": "fast",
            "max_tokens": 16,
            "purpose": "live_test",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["usage"]["total_tokens"] > 0

    response = await live_client.post(
        "/v1/chat",
        json={
            "messages": [
                {
                    "role": "system",
                    "content": "Верни ровно один JSON-объект по схеме: "
                    + json.dumps(SCHEMA, ensure_ascii=False),
                },
                {"role": "user", "content": "ДАННЫЕ: КАМ-01 просрочено 3 из 14; КАМ-02 — 1 из 9."},
            ],
            "tier": "strong",
            "max_tokens": 400,
            "response_schema": SCHEMA,
            "purpose": "live_test",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    text = response.json()["text"]
    start, end = text.find("{"), text.rfind("}")
    assert "headline" in json.loads(text[start : end + 1])
