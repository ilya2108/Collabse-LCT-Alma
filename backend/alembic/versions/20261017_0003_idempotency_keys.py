"""Ключи идемпотентности мутаций и нормализация транспорта интеграций.

Таблица `idempotency_key` (api-contract.md §1.6, data-model.md §9.6) — хранилище
сквозного заголовка Idempotency-Key: область действия ключа (principal, idem_key),
отпечаток запроса, аренда обработки (lock_token, locked_until) и сохранённый
2xx-ответ. Тело ответа хранится Fernet-шифртекстом (bytea): ответы мутаций
содержат ПДн, а по data-model.md §8 ПДн в БД лежат только шифртекстом. TTL 24 ч
(`expires_at`), физическую очистку выполняет CronJob idempotency-purge.

Нормализация `app_setting.integration_mode` (api-contract.md §13.7): транспорт
интеграций — только `http` (outbox PostgreSQL → HTTP + HMAC); значение `stream`
ни на что не влияло и приводится к `http`. Даунгрейд удаляет таблицу, а
нормализацию настройки не откатывает намеренно: прежнее значение не имело эффекта.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001).
_DDL = (
    """
CREATE TABLE idempotency_key (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal             text NOT NULL,
    idem_key              text NOT NULL,
    method                text NOT NULL,
    path                  text NOT NULL,
    request_hash          char(64) NOT NULL,
    status                text NOT NULL DEFAULT 'processing',
    lock_token            uuid,
    locked_until          timestamptz,
    response_status       smallint,
    response_headers      jsonb,
    response_body         bytea,          -- Fernet-шифртекст: ответ может содержать ПДн
    response_body_omitted boolean NOT NULL DEFAULT false,
    first_request_id      text,
    replay_count          integer NOT NULL DEFAULT 0,
    created_at            timestamptz NOT NULL DEFAULT now(),
    completed_at          timestamptz,
    expires_at            timestamptz NOT NULL,
    CONSTRAINT uq_idempotency_key_principal_key UNIQUE (principal, idem_key),
    CONSTRAINT ck_idempotency_key_status CHECK (status IN ('processing', 'completed')),
    CONSTRAINT ck_idempotency_key_completed_response
        CHECK (status <> 'completed' OR response_status IS NOT NULL),
    CONSTRAINT ck_idempotency_key_key_length CHECK (char_length(idem_key) BETWEEN 1 AND 255)
)
""",
    "CREATE INDEX ix_idempotency_key_expires_at ON idempotency_key (expires_at)",
)

# Вынесено модульной константой: тест выполняет её на строке со значением "stream".
_INTEGRATION_MODE_FIX = """
UPDATE app_setting
   SET value = '{"value": "http"}'::jsonb,
       description = 'Транспорт интеграций: http (outbox PostgreSQL → HTTP + HMAC; api-contract.md §13.7)',
       updated_at = now()
 WHERE key = 'integration_mode'
"""


def upgrade() -> None:
    for statement in _DDL:
        op.execute(statement)
    op.execute(_INTEGRATION_MODE_FIX)


def downgrade() -> None:
    # Нормализация integration_mode не откатывается: значение stream не имело эффекта.
    op.execute("DROP TABLE IF EXISTS idempotency_key")
