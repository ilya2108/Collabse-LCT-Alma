"""Защитные механизмы ядра: ПДн в журнале интеграций, IP аудита, контурные секреты."""

from __future__ import annotations

import pytest
from fastapi import Request

from app.core.audit import client_ip
from app.core.config import Settings, get_settings
from app.modules.integrations.service import mask_lead_payload


class TestMaskLeadPayload:
    """Блок lead конверта cms.lead.created не хранится с открытыми ПДн (data-model §8)."""

    def test_person_pii_masked(self) -> None:
        payload = {
            "lead": {
                "client_kind": "person",
                "full_name": "Иванов Иван Иванович",
                "email": "ivanov@example.com",
                "phone": "+7 916 000-11-22",
                "comment": "хочу на курс",
            },
            "program": {"crm_program_id": "x"},
        }
        masked = mask_lead_payload(payload)
        assert masked["lead"]["full_name"] == "И***в И.И."
        assert masked["lead"]["email"] == "i***v@example.com"
        assert "***" in masked["lead"]["phone"]
        # немаскируемые поля и соседние блоки не трогаются
        assert masked["lead"]["comment"] == "хочу на курс"
        assert masked["program"] == {"crm_program_id": "x"}
        # исходный payload не мутируется (конверт ещё нужен обработчику)
        assert payload["lead"]["full_name"] == "Иванов Иван Иванович"

    def test_payload_without_lead_untouched(self) -> None:
        payload = {"status": {"name": "in_progress"}}
        assert mask_lead_payload(payload) is payload

    def test_missing_fields_tolerated(self) -> None:
        masked = mask_lead_payload({"lead": {"client_kind": "company", "inn": "7707"}})
        assert masked["lead"] == {"client_kind": "company", "inn": "7707"}


def _request(headers: dict[str, str], client_host: str = "10.0.0.5") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 4321),
    }
    return Request(scope)


class TestClientIp:
    """X-Forwarded-For — клиентский заголовок: без доверенного прокси игнорируется."""

    def test_spoofed_xff_ignored_without_trusted_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "trusted_proxy", False)
        request = _request({"X-Forwarded-For": "1.2.3.4"})
        assert client_ip(request) == "10.0.0.5"

    def test_trusted_proxy_takes_last_hop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "trusted_proxy", True)
        # ingress дописывает реальный адрес В КОНЕЦ; подложный 1.2.3.4 — слева
        request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
        assert client_ip(request) == "203.0.113.7"

    def test_trusted_proxy_without_header_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "trusted_proxy", True)
        assert client_ip(_request({})) == "10.0.0.5"

    def test_garbage_ip_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "trusted_proxy", True)
        assert client_ip(_request({"X-Forwarded-For": "not-an-ip"})) is None

    def test_default_trust_derived_from_app_env(self) -> None:
        assert Settings(app_env="dev").trust_forwarded_for is False
        assert _k8s_settings().trust_forwarded_for is True


def _k8s_settings(**overrides: str) -> Settings:
    """Settings контура k8s с валидными секретами (дефолты запрещены валидатором)."""
    values: dict[str, str] = {
        "app_env": "k8s",
        "internal_api_token": "a" * 32,
        "lms_webhook_secret": "b" * 32,
        "cms_webhook_secret": "c" * 32,
    }
    values.update(overrides)
    return Settings(**values)


class TestContourSecretsFailClosed:
    """В k8s/prod общеизвестные dev-дефолты секретов роняют старт (по образцу PiiCrypto)."""

    def test_dev_defaults_rejected_in_k8s(self) -> None:
        with pytest.raises(ValueError, match="INTERNAL_API_TOKEN"):
            _k8s_settings(internal_api_token="dev-internal-token")

    def test_empty_webhook_secret_rejected_in_k8s(self) -> None:
        with pytest.raises(ValueError, match="CMS_WEBHOOK_SECRET"):
            _k8s_settings(cms_webhook_secret="  ")

    def test_dev_webhook_default_rejected_in_k8s(self) -> None:
        with pytest.raises(ValueError, match="LMS_WEBHOOK_SECRET"):
            _k8s_settings(lms_webhook_secret="dev-lms-secret")

    def test_real_secrets_accepted_in_k8s(self) -> None:
        assert _k8s_settings().app_env == "k8s"

    def test_dev_and_test_keep_defaults(self) -> None:
        assert Settings(app_env="dev").internal_api_token
        assert Settings(app_env="test").lms_webhook_secret
