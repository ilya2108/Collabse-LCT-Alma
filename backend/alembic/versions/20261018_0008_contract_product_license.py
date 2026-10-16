"""Лицензии в строках договора и импорт «Таблица КАМов».

Шаги:
- `contract_product` — строка «договор × ИТ-продукт» становится лицензией: дата подписания
  (`license_signed_at`), срок в годах (`license_term_years`, 1–99), дата окончания
  (`license_valid_to`: из срока или явно — «до 2027», «31.12.2027»), статус передачи
  лицензии вузу (`transfer_status`: not_started | in_progress | transferred | cancelled,
  NULL — не указан) и момент последней смены статуса (`transfer_status_updated_at`).
  Все поля необязательные: строки договоров, заведённые до миграции, остаются валидными;
- `ix_contract_product_license_valid_to` — частичный индекс под выборки «истекающие
  лицензии»;
- `import_session.entity_type` — в CHECK добавлен тип `kam_catalog` (мастер импорта:
  вуз, ИТ-направление, ИТ-продукт и вендор, договор по номеру, лицензия, статус передачи,
  ответственный КАМ, контакты вуза, комментарий). Имя ограничения прежнее — то, что
  PostgreSQL дал инлайновому CHECK в 0001.

Новых прав нет: импорт идёт под `import:run`, а сервис импорта дополнительно требует
`universities:write` и `contracts:write` (права уже в каталоге F2) — матрица доступа
базовых ролей не меняется.

Даунгрейд: сессии импорта типа `kam_catalog` удаляются (построчные ошибки — каскадом,
ссылки студентов на сессию обнуляются по FK), CHECK возвращается к прежнему списку,
поля лицензий удаляются вместе с данными.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_ENTITY_TYPES_0001 = (
    "'students', 'universities', 'university_contacts', 'counterparties', "
    "'requests_b2b', 'requests_b2c', 'contracts', 'products', 'programs'"
)
_ENTITY_TYPES_0008 = _ENTITY_TYPES_0001 + ", 'kam_catalog'"


def _entity_type_check(types: str) -> str:
    return (
        "ALTER TABLE import_session ADD CONSTRAINT import_session_entity_type_check "
        f"CHECK (entity_type IN ({types}) OR entity_type LIKE 'dictionary:%')"
    )


# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001).
_UPGRADE = (
    # --- лицензия в строке договора -----------------------------------------------------------
    "ALTER TABLE contract_product ADD COLUMN license_signed_at date",
    "ALTER TABLE contract_product ADD COLUMN license_term_years smallint",
    "ALTER TABLE contract_product ADD COLUMN license_valid_to date",
    "ALTER TABLE contract_product ADD COLUMN transfer_status text",
    "ALTER TABLE contract_product ADD COLUMN transfer_status_updated_at timestamptz",
    "ALTER TABLE contract_product ADD CONSTRAINT ck_contract_product_license_term "
    "CHECK (license_term_years IS NULL OR license_term_years BETWEEN 1 AND 99)",
    "ALTER TABLE contract_product ADD CONSTRAINT ck_contract_product_license_dates "
    "CHECK (license_valid_to IS NULL OR license_signed_at IS NULL "
    "OR license_valid_to >= license_signed_at)",
    "ALTER TABLE contract_product ADD CONSTRAINT ck_contract_product_transfer_status "
    "CHECK (transfer_status IS NULL OR transfer_status IN "
    "('not_started', 'in_progress', 'transferred', 'cancelled'))",
    "CREATE INDEX ix_contract_product_license_valid_to ON contract_product (license_valid_to) "
    "WHERE license_valid_to IS NOT NULL",
    # --- тип импорта «Таблица КАМов» ----------------------------------------------------------
    "ALTER TABLE import_session DROP CONSTRAINT IF EXISTS import_session_entity_type_check",
    _entity_type_check(_ENTITY_TYPES_0008),
)

_DOWNGRADE = (
    "DELETE FROM import_session WHERE entity_type = 'kam_catalog'",
    "ALTER TABLE import_session DROP CONSTRAINT IF EXISTS import_session_entity_type_check",
    _entity_type_check(_ENTITY_TYPES_0001),
    "DROP INDEX IF EXISTS ix_contract_product_license_valid_to",
    "ALTER TABLE contract_product DROP CONSTRAINT IF EXISTS ck_contract_product_transfer_status",
    "ALTER TABLE contract_product DROP CONSTRAINT IF EXISTS ck_contract_product_license_dates",
    "ALTER TABLE contract_product DROP CONSTRAINT IF EXISTS ck_contract_product_license_term",
    "ALTER TABLE contract_product DROP COLUMN IF EXISTS transfer_status_updated_at",
    "ALTER TABLE contract_product DROP COLUMN IF EXISTS transfer_status",
    "ALTER TABLE contract_product DROP COLUMN IF EXISTS license_valid_to",
    "ALTER TABLE contract_product DROP COLUMN IF EXISTS license_term_years",
    "ALTER TABLE contract_product DROP COLUMN IF EXISTS license_signed_at",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
