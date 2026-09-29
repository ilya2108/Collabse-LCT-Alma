"""GET /api/v1/stats: счётчики отправок/ошибок/дублей и шаблон deep-link бота."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.bot.deeplink import build_deep_link
from tests.conftest import FakeChannel


def _body(notification_id: str, channel: str = "email") -> dict:
    return {
        "notification_id": notification_id,
        "channel": channel,
        "recipient": "kam@corp.local",
        "text": "Текст уведомления",
    }


async def test_stats_counts_sent_failed_and_duplicates(
    app: FastAPI, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    app.state.channels["email"] = FakeChannel("email")
    failing = FakeChannel("max", fail=True)
    app.state.channels["max"] = failing

    await client.post("/api/v1/send", json=_body("ntf-1"), headers=auth_headers)
    await client.post("/api/v1/send", json=_body("ntf-1"), headers=auth_headers)  # дубль
    await client.post("/api/v1/send", json=_body("ntf-2", "max"), headers=auth_headers)

    response = await client.get("/api/v1/stats", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["service"] == "notification"
    assert payload["uptime_seconds"] >= 0
    assert payload["sent"] == {"email": 1}
    assert payload["failed"] == {"max": 1}
    assert payload["duplicates_suppressed"] == 1
    assert payload["telegram_retries"] == 0
    assert payload["channels"] == {"telegram": "stub", "email": "active", "max": "active"}
    assert payload["bot"]["enabled"] is False
    assert payload["bot"]["username"] == "lct_crm_bot"
    assert payload["bot"]["deep_link_template"] == "https://t.me/lct_crm_bot?start=<code>"
    assert payload["bot"]["commands_total"] == 0


async def test_stats_requires_auth(client: httpx.AsyncClient) -> None:
    missing = await client.get("/api/v1/stats")
    wrong = await client.get("/api/v1/stats", headers={"X-Internal-Token": "wrong"})
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_deep_link_builder() -> None:
    assert build_deep_link("lct_crm_bot", "482913") == "https://t.me/lct_crm_bot?start=482913"
    assert build_deep_link("@lct_crm_bot", "482913") == "https://t.me/lct_crm_bot?start=482913"


@pytest.mark.parametrize("username", ["", "@", "ab", "привет_бот", "bad name"])
def test_deep_link_rejects_invalid_usernames(username: str) -> None:
    with pytest.raises(ValueError):
        build_deep_link(username, "482913")
