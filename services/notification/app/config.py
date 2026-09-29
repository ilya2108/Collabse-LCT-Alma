"""Конфигурация сервиса из переменных окружения (docs/design/deployment.md §3.7, §5)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки читаются из env; имена совпадают с deployment.md (compose и k8s)."""

    model_config = SettingsConfigDict(extra="ignore")

    # Telegram. Пустой токен => бот выключен, сервис работает как чистый relay
    # (каналы email/max/telegram — в режиме заглушек). Важно для CI и демо без интернета.
    telegram_bot_token: str = ""
    # Username бота — deep-link привязки t.me/<bot>?start=<code> (api-contract.md §9.2).
    telegram_bot_username: str = "lct_crm_bot"
    # Прокси до Bot API (http://user:pass@host:port). Нужен, когда прямой доступ к
    # api.telegram.org с площадки закрыт; в проде это та же DMZ-точка egress —
    # весь телеграм-трафик уходит через единственный контролируемый узел.
    telegram_proxy_url: str = ""
    # Альтернатива прокси: свой базовый URL Bot API (reverse-proxy с нейтральным SNI,
    # см. deploy/README). DPI-блокировки по SNI api.telegram.org его не видят.
    telegram_api_base: str = ""

    # Ретраи отправки в Telegram: 429/сеть/5xx Bot API. При исчерпании попыток — 502
    # relay'у, backend-outbox перепланирует доставку своим расписанием ретраев.
    telegram_send_attempts: int = 3
    telegram_retry_base_delay_seconds: float = 0.5

    # Клиентский flood-control исходящих: держимся ниже лимитов Bot API
    # (~30 msg/s суммарно, ~1 msg/s в один чат), чтобы веерная рассылка не ловила 429.
    telegram_send_rate_per_second: float = 25.0
    telegram_per_chat_interval_seconds: float = 1.0

    # Keycloak (client credentials клиента crm-notification, роль svc_notification).
    keycloak_internal_url: str = "http://keycloak:8080"
    keycloak_realm: str = "crm"
    # iss в токенах пользователей (валидация admin-Bearer на GET /api/v1/outbox).
    keycloak_issuer: str = "http://id.crm.local/realms/crm"
    kc_client_id: str = "crm-notification"
    kc_client_secret: str = ""

    # Ядро CRM: команды бота ходят в /api/v1/internal/bot/* под Bearer svc_notification.
    backend_base_url: str = "http://backend:8000"

    # Общий секрет контура: backend -> POST /api/v1/send приходит с X-Internal-Token.
    internal_api_token: str = "dev-internal-token"

    # KeyDB: идемпотентность send и rate-limit команд бота.
    # Пустая строка => KeyDB не используется (деградация в память процесса).
    keydb_url: str = "redis://keydb:6379/0"

    # Публичный адрес CRM — абсолютные ссылки в кнопках Telegram («Открыть в CRM»).
    crm_public_url: str = "http://crm.local"

    log_level: str = "INFO"

    # Идемпотентность POST /api/v1/send: маркер доставки crm:v1:notif:sent:{id}
    # ставится ПОСЛЕ успешной отправки в канал, TTL 7 суток.
    idempotency_ttl_seconds: int = 7 * 24 * 3600
    # In-flight клейм crm:v1:notif:claim:{id} на время доставки: защищает от
    # параллельного дубля, а при крэше посреди отправки истекает сам — ретрай
    # relay'а доставит сообщение заново, а не отбросит как «дубль».
    idempotency_claim_ttl_seconds: int = 60

    # Журнал заглушечных/реальных отправок: последние N записей на канал (GET /api/v1/outbox).
    outbox_per_channel: int = 100

    # Rate-limit команд бота: KeyDB crm:v1:rl:tg:{chat_id} (INCR + EXPIRE 60с).
    bot_rate_limit_per_minute: int = 20

    @property
    def bot_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def keycloak_realm_url(self) -> str:
        return f"{self.keycloak_internal_url.rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def token_endpoint(self) -> str:
        return f"{self.keycloak_realm_url}/protocol/openid-connect/token"

    @property
    def jwks_endpoint(self) -> str:
        return f"{self.keycloak_realm_url}/protocol/openid-connect/certs"

    @property
    def allowed_issuers(self) -> set[str]:
        """iss может быть внешним (id.crm.local, браузерные токены) или внутренним DNS-именем."""
        return {self.keycloak_issuer, self.keycloak_realm_url}
