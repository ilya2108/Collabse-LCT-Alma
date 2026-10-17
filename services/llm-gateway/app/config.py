"""Настройки шлюза из переменных окружения (имена — как в deployment.md и README).

Ключ GigaChat и токен контура хранятся как `SecretStr`: случайный `repr(settings)` в логе
или трейсбеке не раскрывает значения.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_GATEWAY_TOKEN = "dev-llm-token"

# Маркетинговые имена → id моделей в API. «GigaChat 2 Lite» в API называется GigaChat-2:
# модели GigaChat-2-Lite в списке GET /models нет (проверено 17.10.2026 на обоих хостах),
# а такое имя встречается в готовых env и даёт 404 No such model.
MODEL_ALIASES = {"GigaChat-2-Lite": "GigaChat-2", "GigaChat-Lite": "GigaChat"}


class Settings(BaseSettings):
    """Все поля читаются из env без префикса: `GIGACHAT_CREDENTIALS`, `LLM_QUEUE_MAX` и т. д."""

    # hide_input_in_errors: ошибка валидации на старте иначе печатает весь вход из env,
    # включая GIGACHAT_CREDENTIALS, в лог пода
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    # dev | test | k8s | prod. В контуре (k8s/prod) включаются fail-closed проверки ниже.
    app_env: str = "dev"

    # Ожидаемый X-Internal-Token: backend → POST /v1/chat (Secret crm-llm, ключ LLM_GATEWAY_TOKEN).
    llm_gateway_token: SecretStr = SecretStr(DEV_GATEWAY_TOKEN)

    # gigachat — реальный API Сбера; stub — детерминированный ответ без сети (CI, демо офлайн).
    llm_provider: Literal["gigachat", "stub"] = "gigachat"

    # Authorization key из кабинета GigaChat API (base64 от client_id:client_secret).
    # Пусто => /v1/chat отвечает 503 llm_not_configured, CRM предлагает сводку по правилам.
    gigachat_credentials: SecretStr = SecretStr("")
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model_strong: str = "GigaChat-2-Pro"
    # GigaChat 2 Lite (id в API — GigaChat-2)
    gigachat_model_fast: str = "GigaChat-2"
    # On-prem GigaChat или другой хост API подключается этими двумя URL без правки кода.
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_api_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    # PEM корневого и выпускающего сертификатов НУЦ Минцифры (Russian Trusted Root/Sub CA):
    # без них цепочка *.devices.sberbank.ru не проверяется стандартным хранилищем certifi.
    gigachat_ca_bundle: str = ""
    gigachat_verify_ssl: bool = True
    # Необязательный HTTPS-прокси (паттерн VM-стенда для Telegram).
    gigachat_proxy_url: str = ""

    # Бесплатный тариф GIGACHAT_API_PERS — ровно один параллельный поток.
    llm_max_concurrency: int = Field(1, ge=1)
    # Ждущих сверх лимита больше этого числа => 429 queue_full.
    llm_queue_max: int = Field(8, ge=0)
    # Дольше ждать свободный слот => 503 queue_timeout.
    llm_queue_timeout_seconds: float = Field(180.0, gt=0)
    # Бюджет на ВСЕ попытки после получения слота, включая OAuth (p50 GigaChat-2-Pro ≈ 64 с).
    llm_timeout_seconds: float = Field(120.0, gt=0)
    llm_connect_timeout_seconds: float = Field(10.0, gt=0)
    # Всего попыток на ретраибельные сбои (сеть, 429, 5xx) в пределах llm_timeout_seconds.
    llm_retry_attempts: int = Field(3, ge=1)
    llm_retry_base_delay_seconds: float = Field(2.0, ge=0)
    # total_tokens за сутки МСК; 0 — без лимита. Счётчик в памяти: рестарт пода его обнуляет.
    llm_daily_token_cap: int = Field(200_000, ge=0)
    # Сумма длин content всех сообщений; больше => 400 validation_error.
    llm_max_input_chars: int = Field(60_000, ge=1)
    # Верхняя граница max_tokens в запросе.
    llm_max_output_tokens: int = Field(2048, ge=16)

    log_level: str = "INFO"

    @field_validator("gigachat_model_strong", "gigachat_model_fast")
    @classmethod
    def _model_id(cls, value: str) -> str:
        value = value.strip()
        return MODEL_ALIASES.get(value, value)

    @model_validator(mode="after")
    def _fail_closed_in_cluster(self) -> Settings:
        """В контуре слабый токен или выключенная проверка TLS — ошибка старта, а не тихий риск."""
        if self.app_env in ("k8s", "prod"):
            token = self.llm_gateway_token.get_secret_value().strip()
            if not token or token == DEV_GATEWAY_TOKEN:
                raise ValueError(
                    f"APP_ENV={self.app_env}: LLM_GATEWAY_TOKEN пуст или равен dev-дефолту. "
                    "Смонтируйте Secret crm-llm (make k8s-secrets) или задайте переменную"
                )
            if not self.gigachat_verify_ssl:
                raise ValueError(
                    f"APP_ENV={self.app_env}: GIGACHAT_VERIFY_SSL=false запрещён — "
                    "подключите бандл НУЦ через GIGACHAT_CA_BUNDLE"
                )
        return self

    @property
    def credentials(self) -> str:
        return self.gigachat_credentials.get_secret_value().strip()

    @property
    def dev_token_with_real_key(self) -> bool:
        """Настоящий ключ GigaChat при пустом или общеизвестном dev-токене — при ЛЮБОМ
        APP_ENV: dev-стенд, опубликованный в локальную сеть, иначе раздал бы ключ всем, кто
        знает «dev-llm-token» из репозитория. Такой шлюз отклоняет все вызовы (401)."""
        token = self.llm_gateway_token.get_secret_value().strip()
        return (
            self.llm_provider == "gigachat"
            and bool(self.credentials)
            and (not token or token == DEV_GATEWAY_TOKEN)
        )

    @property
    def configured(self) -> bool:
        """Есть ли чем ходить в GigaChat (stub настроен всегда)."""
        return self.llm_provider == "stub" or bool(self.credentials)

    def model_for(self, tier: str) -> str:
        return self.gigachat_model_fast if tier == "fast" else self.gigachat_model_strong
