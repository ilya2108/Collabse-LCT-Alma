"""Fail-closed настройки в контуре и поведение без ключа GigaChat."""
from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.conftest import CREDENTIALS, FakeGigaChat, chat_payload


@pytest.mark.parametrize("app_env", ["k8s", "prod"])
@pytest.mark.parametrize("token", ["dev-llm-token", "", "   "])
def test_weak_token_in_cluster_fails(app_env: str, token: str) -> None:
    with pytest.raises(ValidationError, match="LLM_GATEWAY_TOKEN"):
        Settings(app_env=app_env, llm_gateway_token=token)


def test_validation_error_does_not_echo_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "k8s")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", CREDENTIALS)
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "dev-llm-token")
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert CREDENTIALS not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_verify_ssl_off_in_cluster_fails() -> None:
    with pytest.raises(ValidationError, match="GIGACHAT_VERIFY_SSL"):
        Settings(app_env="k8s", llm_gateway_token="a" * 64, gigachat_verify_ssl=False)


def test_strong_token_in_cluster_ok_and_dev_defaults_allowed() -> None:
    assert Settings(app_env="k8s", llm_gateway_token="a" * 64).configured is False
    dev = Settings(app_env="dev")
    assert dev.llm_gateway_token.get_secret_value() == "dev-llm-token"
    assert dev.gigachat_verify_ssl is True


def test_marketing_model_names_mapped_to_api_ids() -> None:
    settings = Settings(
        gigachat_model_fast="GigaChat-2-Lite", gigachat_model_strong=" GigaChat-2-Pro "
    )
    assert settings.model_for("fast") == "GigaChat-2"
    assert settings.model_for("strong") == "GigaChat-2-Pro"
    assert Settings().gigachat_model_fast == "GigaChat-2"


def test_settings_from_env_and_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", CREDENTIALS)
    monkeypatch.setenv("GIGACHAT_MODEL_STRONG", "GigaChat-2-Max")
    monkeypatch.setenv("LLM_QUEUE_MAX", "3")
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "token-from-env")
    settings = Settings()
    assert settings.configured is True
    assert settings.model_for("strong") == "GigaChat-2-Max"
    assert settings.llm_queue_max == 3
    assert CREDENTIALS not in repr(settings)
    assert "token-from-env" not in repr(settings)


async def test_empty_key_is_503_not_configured(
    build, fake: FakeGigaChat, auth: dict[str, str]
) -> None:
    client, _ = await build(gigachat_credentials="")
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"
    assert fake.oauth_requests == []
    usage = (await client.get("/v1/usage", headers=auth)).json()
    assert usage["configured"] is False
    assert usage["requests"]["error"]["llm_not_configured"] == 1


async def test_custom_urls_for_on_prem(build, fake: FakeGigaChat, auth: dict[str, str]) -> None:
    client, _ = await build(
        gigachat_api_url="https://gigachat.corp.local/api/v1/",
        gigachat_auth_url="https://auth.corp.local/oauth",
    )
    response = await client.post("/v1/chat", json=chat_payload(), headers=auth)
    assert response.status_code == 200
    assert str(fake.oauth_requests[0].url) == "https://auth.corp.local/oauth"
    assert str(fake.chat_requests[0].url) == "https://gigachat.corp.local/api/v1/chat/completions"


async def test_client_with_proxy_builds() -> None:
    """С прокси и без тестового транспорта клиент строится без ошибок (сеть не трогаем)."""
    from app.tls import build_http_client, build_tls

    settings = Settings(gigachat_proxy_url="http://proxy.local:3128")
    client = build_http_client(settings, build_tls(settings))
    assert isinstance(client, httpx.AsyncClient)
    await client.aclose()
