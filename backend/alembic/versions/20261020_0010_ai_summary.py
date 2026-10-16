"""Сводки ИИ в отчёте «Активность КАМов»: задачи `ai_summary_job` и право `reports:ai_summary`.

Шаги:
- `ai_summary_job` — задача сводки (modules/ai_summary): факты, которые уходят в GigaChat
  через llm-gateway (только агрегаты отчётов и псевдонимы КАМов, без ПДн), псевдонимы
  `{"КАМ-01": "<uuid>"}` (ФИО в таблице нет — подставляются при чтении), статус и результат.
  Таблица, а не KeyDB: POST и опрос попадают в разные реплики, KeyDB работает без
  persistence и с вытеснением LRU. Частичный UNIQUE `uq_ai_summary_job_inflight` по
  `cache_key` среди `queued`/`running` — атомарная дедупликация задач в полёте между
  репликами (`INSERT … ON CONFLICT DO NOTHING`); `ix_ai_summary_job_cache` — поиск готовой
  сводки за последний час; `ix_ai_summary_job_queue` — очередь и «перед вами N сводок»;
  `ix_ai_summary_job_created` — ленивая очистка старше 7 дней. Сброс стенда очищает
  таблицу сам (её нет в `RESET_KEEP_TABLES`);
- право `reports:ai_summary` («Сводки ИИ по отчёту КАМов», с областью, требует
  `reports:read`) получают `admin` и `head_kam`; у `kam` и `observer` его нет. Выдача —
  здесь: `seed_rbac_catalog` наборы существующих системных ролей не меняет (кроме admin).
  Доступ базовых ролей к прежним эндпоинтам не меняется.

Даунгрейд: право удаляется вместе со связями ролей (каскад), затем таблица.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001).
_UPGRADE = (
    # --- задачи сводок ----------------------------------------------------------------------
    """
CREATE TABLE ai_summary_job (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind           text NOT NULL CHECK (kind IN ('team','kam')),
    kam_id         uuid REFERENCES app_user(id) ON DELETE CASCADE,
    scope_key      text NOT NULL,
    params         jsonb NOT NULL,
    mode           text NOT NULL CHECK (mode IN ('auto','rules')),
    provider       text NOT NULL CHECK (provider IN ('gigachat','rules','stub')),
    prompt_version text NOT NULL,
    input_sha256   text NOT NULL,
    cache_key      text NOT NULL,
    status         text NOT NULL CHECK (status IN ('queued','running','done','failed')),
    facts          jsonb NOT NULL,
    aliases        jsonb NOT NULL,
    result         jsonb,
    error_code     text,
    error_message  text,
    model          text,
    tokens_in      int,
    tokens_out     int,
    latency_ms     int,
    attempts       int NOT NULL DEFAULT 0,
    requested_by   uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    created_at     timestamptz NOT NULL DEFAULT now(),
    started_at     timestamptz,
    finished_at    timestamptz,
    CONSTRAINT ck_ai_summary_job_kam CHECK ((kind = 'kam') = (kam_id IS NOT NULL))
)
""",
    "CREATE UNIQUE INDEX uq_ai_summary_job_inflight ON ai_summary_job (cache_key) "
    "WHERE status IN ('queued','running')",
    "CREATE INDEX ix_ai_summary_job_cache ON ai_summary_job (cache_key, finished_at DESC) "
    "WHERE status = 'done'",
    "CREATE INDEX ix_ai_summary_job_queue ON ai_summary_job (created_at) "
    "WHERE status IN ('queued','running')",
    "CREATE INDEX ix_ai_summary_job_created ON ai_summary_job (created_at)",
    # --- право «Сводки ИИ по отчёту КАМов» ------------------------------------------------
    """
INSERT INTO app_permission (code, group_code, name, description, is_scoped, sort_order)
VALUES (
    'reports:ai_summary',
    'reports',
    'Сводки ИИ по отчёту КАМов',
    'Кнопка «Сводка ИИ» в отчёте «Активность КАМов»: текстовая сводка фактов по команде и '
    'по КАМу (GigaChat получает только агрегаты и псевдонимы; без него — сводка по правилам)',
    true,
    465
)
ON CONFLICT (code) DO NOTHING
""",
    """
INSERT INTO app_role_permission (role_id, permission_code)
SELECT r.id, 'reports:ai_summary'
FROM app_role r
WHERE r.code IN ('admin', 'head_kam')
ON CONFLICT DO NOTHING
""",
)

_DOWNGRADE = (
    "DELETE FROM app_permission WHERE code = 'reports:ai_summary'",
    "DROP TABLE IF EXISTS ai_summary_job",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
