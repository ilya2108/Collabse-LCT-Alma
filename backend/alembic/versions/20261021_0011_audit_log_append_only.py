"""Журнал аудита только дополняется: UPDATE, DELETE и TRUNCATE `audit_log` запрещены в БД.

Шаги:
- функция `audit_log_append_only()` — RAISE с SQLSTATE 42501 (insufficient_privilege) и
  текстом «audit_log is append-only»; INSERT и чтение не затрагиваются;
- `trg_audit_log_append_only` — построчный BEFORE UPDATE OR DELETE: ловит и прямые запросы,
  и ORM, и каскадные действия внешних ключей, если их когда-нибудь добавят (сейчас у
  `actor_user_id` NO ACTION — удаление автора записи и так отклоняется);
- `trg_audit_log_no_truncate` — BEFORE TRUNCATE на уровне оператора (в том числе
  `TRUNCATE app_user CASCADE`).

Приложение журнал не меняет (`core/audit.py` только вставляет), сброс стенда его сохраняет
(`RESET_KEEP_TABLES` в tools/seed.py), ретеншна нет. Очистка нужна только одноразовым
базам: тестовая фикстура `truncate_and_restore` выключает TRUNCATE-триггер на время
очистки в своей транзакции (право владельца таблицы). От владельца схемы и суперпользователя
БД триггер не защищает — это защита от ошибок кода и случайных запросов; разделение ролей
«миграции / приложение» — шаг пилота (docs/evidence/compliance.md).

Даунгрейд: триггеры, затем функция (даунгрейд 0001 функцию не удаляет — без этого повторный
upgrade упал бы; CREATE OR REPLACE — та же страховка).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001). Тело функции в $$ — одна команда;
# без «%» и «:слово» — op.execute оборачивает строку в text().
_UPGRADE = (
    """
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'insufficient_privilege',
        MESSAGE = 'audit_log is append-only: ' || TG_OP || ' запрещён',
        HINT = 'Журнал аудита только дополняется (миграция 0011)';
END
$$
""",
    """
CREATE TRIGGER trg_audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()
""",
    """
CREATE TRIGGER trg_audit_log_no_truncate
BEFORE TRUNCATE ON audit_log
FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only()
""",
    "COMMENT ON TABLE audit_log IS "
    "'Append-only: UPDATE, DELETE и TRUNCATE запрещены триггерами (миграция 0011)'",
)

_DOWNGRADE = (
    "COMMENT ON TABLE audit_log IS NULL",
    "DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON audit_log",
    "DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log",
    "DROP FUNCTION IF EXISTS audit_log_append_only()",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
