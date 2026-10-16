"""Пробы: /healthz всегда ok; /readyz — 200 и диагностика без проверки GigaChat."""
from __future__ import annotations

from pathlib import Path

import certifi
import httpx

from tests.conftest import FakeGigaChat, chat_payload


def _first_certifi_pem() -> str:
    text = Path(certifi.where()).read_text(encoding="ascii")
    end = text.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")
    start = text.rindex("-----BEGIN CERTIFICATE-----", 0, end)
    return text[start:end] + "\n"


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_not_configured_is_still_ready(build) -> None:
    client, _ = await build(gigachat_credentials="")
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {
            "provider": "gigachat",
            "configured": False,
            "ca_bundle": "not_set",
            "token": "none",
        },
    }


async def test_readyz_ca_bundle_missing(build, tmp_path: Path) -> None:
    client, _ = await build(gigachat_ca_bundle=str(tmp_path / "нет-такого.pem"))
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"]["ca_bundle"] == "missing"


async def test_readyz_ca_bundle_loaded_and_token_cached(
    build, tmp_path: Path, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(_first_certifi_pem().replace("\n", "\r\n").encode("ascii"))
    client, _ = await build(gigachat_ca_bundle=str(bundle))
    checks = (await client.get("/readyz")).json()["checks"]
    assert checks["ca_bundle"] == "loaded"
    assert checks["token"] == "none"

    assert (await client.post("/v1/chat", json=chat_payload(), headers=auth)).status_code == 200
    assert (await client.get("/readyz")).json()["checks"]["token"] == "cached"


async def test_readyz_does_not_call_gigachat(client: httpx.AsyncClient, fake: FakeGigaChat) -> None:
    for _ in range(3):
        assert (await client.get("/readyz")).status_code == 200
    assert fake.oauth_requests == [] and fake.chat_requests == []
