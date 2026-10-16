"""Индекс журнала напоминаний о зависших заявках (вкладка «Просрочки», api-contract.md §7.2.2).

Только индекс, схема не меняется:
- `ix_notification_stuck_created (created_at) WHERE event_type = 'request.stuck'` — начало
  журнала напоминаний (`min(created_at)` по `request.stuck`): по нему вкладка «Просрочки»
  решает, с какой даты метрики напоминаний достоверны. Без индекса запрос читает весь
  журнал `notification`, который растёт с каждым событием любого типа; частичный индекс
  содержит только напоминания и отдаёт минимум одним чтением.

Выборка напоминаний по заявкам за окно уже покрыта `ix_notification_entity
(entity_type, entity_id, created_at)` (0001). Корректность отчёта от индекса не зависит.
`CONCURRENTLY` не нужен: миграция выполняется в транзакции Alembic.
Даунгрейд удаляет индекс.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_notification_stuck_created",
        "notification",
        ["created_at"],
        postgresql_where=sa.text("event_type = 'request.stuck'"),
    )


def downgrade() -> None:
    op.drop_index("ix_notification_stuck_created", table_name="notification")
