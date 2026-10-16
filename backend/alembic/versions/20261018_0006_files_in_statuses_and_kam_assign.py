"""Файлы в статусах заявки, право «Назначение КАМа вуза», порог зависания этапа до 365 дней.

Шаги:
- `file_object.stage_history_id` → `deal_stage_history(id)` ON DELETE SET NULL: файл заявки
  привязан к строке истории — переходу, на котором его приложили, либо входу в этап, в
  котором он загружен из вкладки «Файлы». Таймлайн показывает файл под своим переходом.
  Частичный индекс — только у привязанных файлов. Бэкфилл: у уже загруженных файлов заявок
  — последняя строка истории заявки не позже загрузки файла (этап, в котором заявка тогда
  была);
- право `universities:assign` («Назначение ответственного КАМа за вуз»): смена
  `university.kam_user_id` больше не идёт через «свой или ничей вуз». Право получают роли,
  которые и раньше правили чужие вузы (`crm:write_any`: head_kam, admin и свои роли
  руководителей) — доступ базовых ролей к эндпоинтам не меняется, КАМ теряет только
  смену ответственного;
- CHECK порога зависания этапа `workflow_stage.stuck_threshold_days`: 1–365 вместо 1–60
  (долгие этапы «Обучение», «Сопровождение внедрения»). Порог процесса не меняется.

Даунгрейд: пороги этапов больше 60 приводятся к 60 до возврата прежнего CHECK; право
удаляется вместе со связями ролей (каскад); колонка и индекс удаляются.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001).
_UPGRADE = (
    # --- файлы в статусах ------------------------------------------------------------------
    "ALTER TABLE file_object ADD COLUMN stage_history_id uuid "
    "REFERENCES deal_stage_history(id) ON DELETE SET NULL",
    "CREATE INDEX ix_file_object_stage_history ON file_object (stage_history_id) "
    "WHERE stage_history_id IS NOT NULL",
    """
UPDATE file_object f SET stage_history_id = (
    SELECT h.id FROM deal_stage_history h
    WHERE h.deal_id = f.entity_id AND h.created_at <= f.created_at
    ORDER BY h.created_at DESC, h.id DESC
    LIMIT 1
)
WHERE f.entity_type = 'deal' AND f.entity_id IS NOT NULL AND f.stage_history_id IS NULL
""",
    # --- право «Назначение ответственного КАМа за вуз» --------------------------------------
    """
INSERT INTO app_permission (code, group_code, name, description, is_scoped, sort_order)
VALUES (
    'universities:assign',
    'crm',
    'Назначение ответственного КАМа за вуз',
    'Смена КАМа вуза (в том числе при создании) и передача вузов в портфеле КАМа; '
    'без этого права КАМ заводит вуз только на себя',
    false,
    195
)
ON CONFLICT (code) DO NOTHING
""",
    """
INSERT INTO app_role_permission (role_id, permission_code)
SELECT DISTINCT r.id, 'universities:assign'
FROM app_role r
LEFT JOIN app_role_permission p
       ON p.role_id = r.id AND p.permission_code = 'crm:write_any'
WHERE r.code = 'admin' OR p.role_id IS NOT NULL
ON CONFLICT DO NOTHING
""",
    # --- порог зависания этапа до 365 дней ---------------------------------------------------
    "ALTER TABLE workflow_stage DROP CONSTRAINT workflow_stage_stuck_threshold_days_check",
    "ALTER TABLE workflow_stage ADD CONSTRAINT workflow_stage_stuck_threshold_days_check "
    "CHECK (stuck_threshold_days IS NULL OR stuck_threshold_days BETWEEN 1 AND 365)",
)

_DOWNGRADE = (
    "UPDATE workflow_stage SET stuck_threshold_days = 60 WHERE stuck_threshold_days > 60",
    "ALTER TABLE workflow_stage DROP CONSTRAINT workflow_stage_stuck_threshold_days_check",
    "ALTER TABLE workflow_stage ADD CONSTRAINT workflow_stage_stuck_threshold_days_check "
    "CHECK (stuck_threshold_days IS NULL OR stuck_threshold_days BETWEEN 1 AND 60)",
    "DELETE FROM app_permission WHERE code = 'universities:assign'",
    "DROP INDEX IF EXISTS ix_file_object_stage_history",
    "ALTER TABLE file_object DROP COLUMN IF EXISTS stage_history_id",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
