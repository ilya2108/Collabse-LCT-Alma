"""Индексы отчёта «Активность КАМов» (api-contract.md §7.2.1): только индексы, схема не меняется.

Отчёт выбирает заявки КАМа за период по ВСЕМ статусам (кроме архива) и считает
мероприятия по парам «КАМ × вуз»:
- `ix_deal_responsible_created (responsible_user_id, created_at) WHERE status <> 'archived'`
  — выборка «заявки КАМов» с отсечкой `created_at < конец периода`. Есть только частичный
  `ix_deal_responsible` по `status = 'open'` (0001) — для закрытых заявок не подходит, а
  `ix_deal_pipeline_responsible` (0004) начинается с `pipeline` и не обслуживает
  `responsible_user_id IN (…)` без фильтра по процессу;
- `ix_activity_university`, `ix_activity_responsible` — у `activity` индексов по вузу и
  ответственному нет (только `ix_activity_fedproject`).

История переходов, комментарии и файлы заявки уже покрыты `ix_dsh_deal`,
`ix_deal_comment_deal`, `ix_file_object_entity` (0001) — LATERAL «последняя строка
истории до даты» идёт по `ix_dsh_deal`. Корректность отчёта от индексов не зависит.
`CONCURRENTLY` не нужен: таблицы малы, миграция выполняется в транзакции Alembic.
Даунгрейд удаляет индексы.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_UPGRADE = (
    "CREATE INDEX IF NOT EXISTS ix_deal_responsible_created "
    "ON deal (responsible_user_id, created_at) WHERE status <> 'archived'",
    "CREATE INDEX IF NOT EXISTS ix_activity_university "
    "ON activity (university_id) WHERE university_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_activity_responsible "
    "ON activity (responsible_user_id) WHERE responsible_user_id IS NOT NULL",
)

_DOWNGRADE = (
    "DROP INDEX IF EXISTS ix_activity_responsible",
    "DROP INDEX IF EXISTS ix_activity_university",
    "DROP INDEX IF EXISTS ix_deal_responsible_created",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
