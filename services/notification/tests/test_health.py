"""Health-пробы: liveness всегда ok; readiness в режиме relay не требует бота и Keycloak."""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.parametrize("path", ["/healthz", "/health/live", "/healthz/live"])
async def test_liveness(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/readyz", "/health/ready", "/healthz/ready"])
async def test_readiness_relay_mode(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["bot"] == "disabled"
    assert payload["checks"]["keycloak"] == "not_required"
