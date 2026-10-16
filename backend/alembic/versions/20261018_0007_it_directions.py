"""Справочник ИТ-направлений: программа — по одному направлению, продукт — по нескольким.

Шаги:
- `it_direction` — каталог ИТ-направлений обучения (DevOps, QA, анализ данных…): код и
  название уникальны без учёта регистра, `sort_order` — порядок в списках и фильтрах,
  удаление мягкое (`is_active`) — на направление ссылаются программы, продукты и отчёты
  прошлых периодов;
- `program.direction_id` → `it_direction(id)` ON DELETE RESTRICT (N:1, необязательное);
- `product_direction` — M:N «ИТ-продукт помогает учить по одному или нескольким
  направлениям»: удаление продукта снимает связи (CASCADE), направление со связями
  физически не удаляется (RESTRICT);
- описание права `admin:dictionaries` дополнено: этим правом правится и справочник
  ИТ-направлений (чтение направлений доступно всем ролям). Новых прав нет — матрица
  доступа базовых ролей не меняется.

Направления демо-стенда и их привязку к программам и продуктам засевает
`python -m app.tools.seed`; миграция данных каталога не создаёт.

Даунгрейд: колонка, связи и таблица удаляются вместе с данными направлений; описание
права возвращается к прежнему.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001).
_UPGRADE = (
    # --- каталог ИТ-направлений -------------------------------------------------------------
    """
CREATE TABLE it_direction (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        text NOT NULL CHECK (btrim(code) <> ''),
    name        text NOT NULL CHECK (btrim(name) <> ''),
    description text,
    sort_order  int  NOT NULL DEFAULT 100,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
)
""",
    "CREATE UNIQUE INDEX uq_it_direction_code ON it_direction (lower(code))",
    "CREATE UNIQUE INDEX uq_it_direction_name ON it_direction (lower(name))",
    # --- программа: одно направление ----------------------------------------------------------
    "ALTER TABLE program ADD COLUMN direction_id uuid "
    "REFERENCES it_direction(id) ON DELETE RESTRICT",
    "CREATE INDEX ix_program_direction ON program (direction_id) WHERE direction_id IS NOT NULL",
    # --- продукт: несколько направлений -------------------------------------------------------
    """
CREATE TABLE product_direction (
    product_id   uuid NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    direction_id uuid NOT NULL REFERENCES it_direction(id) ON DELETE RESTRICT,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, direction_id)
)
""",
    "CREATE INDEX ix_product_direction_direction ON product_direction (direction_id)",
    # --- право справочников: описание ---------------------------------------------------------
    "UPDATE app_permission SET description = "
    "'Справочники системы, в том числе каталог ИТ-направлений' "
    "WHERE code = 'admin:dictionaries'",
)

_DOWNGRADE = (
    "UPDATE app_permission SET description = 'Справочники системы' "
    "WHERE code = 'admin:dictionaries'",
    "DROP TABLE IF EXISTS product_direction",
    "DROP INDEX IF EXISTS ix_program_direction",
    "ALTER TABLE program DROP COLUMN IF EXISTS direction_id",
    "DROP TABLE IF EXISTS it_direction",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
