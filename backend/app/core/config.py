"""Конфигурация приложения из переменных окружения.

Имена переменных зафиксированы в docs/design/deployment.md (§3.7 ConfigMap `crm-config`,
Secrets `crm-db`/`crm-pii`/`crm-s3`/`crm-internal`; §5 docker-compose). Поля объявлены в
lower_snake_case — pydantic-settings сопоставляет их с UPPER_CASE env-переменными
без регистра.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# общеизвестные dev-дефолты контурных секретов: вне dev/test они запрещены
_DEV_SECRET_DEFAULTS = frozenset({"dev-internal-token", "dev-lms-secret", "dev-cms-secret"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- общее ---------------------------------------------------------------
    app_env: str = "dev"  # dev | k8s | test
    log_level: str = "INFO"
    # доверять X-Forwarded-For (последний hop) — только за известным прокси;
    # None → выводится из app_env: в k8s ingress-nginx перезаписывает заголовок
    # реальным адресом клиента, вне k8s заголовок полностью подконтролен клиенту
    trusted_proxy: bool | None = None

    # --- PostgreSQL (Secret crm-db) -------------------------------------------
    database_url: str = "postgresql+asyncpg://crm:crm@localhost:5432/crm"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # --- KeyDB ----------------------------------------------------------------
    keydb_url: str = "redis://localhost:6379/0"
    # короткий connect-timeout: недоступность кэша деградирует в чтение из PG
    keydb_connect_timeout: float = 0.05

    # --- MinIO / S3 (Secret crm-s3) --------------------------------------------
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_files: str = "crm-files"
    s3_bucket_imports: str = "crm-imports"
    s3_bucket_exports: str = "crm-exports"
    # proxy — файлы отдаёт backend потоком; presigned — 302 на MinIO (TTL 10 мин)
    file_delivery: str = "proxy"

    # --- Keycloak ---------------------------------------------------------------
    keycloak_internal_url: str = "http://localhost:8080"  # откуда качать JWKS
    keycloak_issuer: str = "http://localhost:8080/realms/crm"  # ожидаемый iss в JWT
    keycloak_realm: str = "crm"
    # допустимые audience access-токена (aud или azp); Keycloak по умолчанию ставит "account"
    keycloak_audiences: str = "account,crm-frontend"
    jwks_cache_ttl_seconds: int = 600  # кэш JWKS 10 минут (api-contract.md §1.2)
    # confidential-клиент с service-account ролью view-users — только для
    # POST /admin/users/sync (опционально; штатная синхронизация — upsert при логине)
    keycloak_admin_client_id: str = ""
    keycloak_admin_client_secret: str = ""

    # --- Telegram-бот -------------------------------------------------------------
    # @username бота для deep-link привязки (api-contract.md §9.2); пусто — без ссылки
    telegram_bot_username: str = "lct_crm_bot"

    # --- соседние сервисы контура ------------------------------------------------
    lms_base_url: str = "http://localhost:8020"
    cms_base_url: str = "http://localhost:8030"
    notification_base_url: str = "http://localhost:8010"
    backend_base_url: str = "http://localhost:8000"

    # --- ПДн, 152-ФЗ (Secret crm-pii) ---------------------------------------------
    # список Fernet-ключей через запятую, первый — активный (MultiFernet-ротация)
    pii_fernet_keys: str = ""
    # отдельный ключ >= 32 байт для blind-index HMAC-SHA256
    pii_hmac_key: str = ""

    # --- межсервисная аутентификация (Secret crm-internal) --------------------------
    internal_api_token: str = "dev-internal-token"
    lms_webhook_secret: str = "dev-lms-secret"
    cms_webhook_secret: str = "dev-cms-secret"

    # --- зависшие заявки ------------------------------------------------------------
    # сидирует app_setting['stuck_threshold_days_default']; рабочие пороги живут в БД
    stuck_default_days: int = Field(default=14, ge=1, le=60)

    @model_validator(mode="after")
    def _no_dev_secrets_outside_dev(self) -> Settings:
        """Fail-closed по образцу PiiCrypto: в контуре (k8s/prod) непустые
        общеизвестные dev-дефолты равносильны отсутствию секрета — падаем на старте,
        а не молча аутентифицируем по токену из публичного репозитория."""
        if self.app_env in ("k8s", "prod"):
            weak = [
                name
                for name, value in (
                    ("INTERNAL_API_TOKEN", self.internal_api_token),
                    ("LMS_WEBHOOK_SECRET", self.lms_webhook_secret),
                    ("CMS_WEBHOOK_SECRET", self.cms_webhook_secret),
                )
                if not value.strip() or value in _DEV_SECRET_DEFAULTS
            ]
            if weak:
                raise ValueError(
                    f"APP_ENV={self.app_env}: секреты {', '.join(weak)} пусты или равны "
                    "dev-дефолтам. Смонтируйте Secret crm-internal (make k8s-secrets) "
                    "или задайте переменные окружения"
                )
        return self

    @property
    def trust_forwarded_for(self) -> bool:
        if self.trusted_proxy is not None:
            return self.trusted_proxy
        return self.app_env == "k8s"

    @property
    def keycloak_audience_list(self) -> list[str]:
        return [a.strip() for a in self.keycloak_audiences.split(",") if a.strip()]

    @property
    def pii_fernet_key_list(self) -> list[str]:
        return [k.strip() for k in self.pii_fernet_keys.split(",") if k.strip()]

    @property
    def jwks_url(self) -> str:
        return (
            f"{self.keycloak_internal_url.rstrip('/')}/realms/"
            f"{self.keycloak_realm}/protocol/openid-connect/certs"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
