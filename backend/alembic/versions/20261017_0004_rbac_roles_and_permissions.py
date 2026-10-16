"""Роли и права в БД Альмы: каталог прав, роли, связь «роль — право», область B2B/B2C.

Источник истины назначений ролей — БД Альмы (`app_user.roles`), Keycloak отвечает только
за вход (Р-F2-1). Права определяет код (`app.core.rbac_catalog`): право без проверки в коде
ничего не значит, поэтому `app_permission` создаёт только миграция, а интерфейс собирает
из этих прав роли. Каталог ниже — литеральная копия кода на момент миграции (как маски
в 0002): миграция самодостаточна и не меняется вместе с кодом; расхождение БД и кода ловит
интеграционный тест tests/db/test_rbac_db.py, а новые права следующих версий добавляет
`app.modules.access.bootstrap.seed_rbac_catalog` (идемпотентный upsert).

Шаги:
- DDL `app_permission`, `app_role` (область: `workflow_types` ⊆ {b2b,b2c}, `record_scope`
  all/own), `app_role_permission` и индекс `ix_deal_pipeline_responsible` под фильтр
  области «процесс [+ только свои]»;
- 60 прав и 4 системные роли (admin — заблокирована) с наборами прав по умолчанию:
  доступ базовых ролей к каждому эндпоинту совпадает с прежней матрицей require_roles;
- из `app_user.roles` убираются коды, которых нет в каталоге ролей (сервисные и мусор);
  назначения 4 системных ролей сохраняются как есть.

Даунгрейд удаляет таблицы и индекс; массив `app_user.roles` остаётся (Keycloak снова
станет перезаписывать роли при входе).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# По одной команде на op.execute: asyncpg не выполняет несколько команд
# в одном prepared statement (см. _statements в 0001).
_DDL = (
    """
CREATE TABLE app_permission (
    code        text PRIMARY KEY,
    group_code  text NOT NULL,
    name        text NOT NULL,
    description text,
    is_scoped   boolean NOT NULL DEFAULT false,
    sort_order  integer NOT NULL DEFAULT 0
)
""",
    """
CREATE TABLE app_role (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code           text NOT NULL,
    name           text NOT NULL,
    description    text,
    is_system      boolean NOT NULL DEFAULT false,
    is_locked      boolean NOT NULL DEFAULT false,
    workflow_types text[]  NOT NULL DEFAULT '{b2b,b2c}',
    record_scope   text    NOT NULL DEFAULT 'all',
    created_by     uuid REFERENCES app_user(id) ON DELETE SET NULL,
    updated_by     uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_app_role_code CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$' AND code !~ '^svc_'),
    CONSTRAINT ck_app_role_workflow_types CHECK (
        cardinality(workflow_types) >= 1 AND workflow_types <@ ARRAY['b2b','b2c']::text[]),
    CONSTRAINT ck_app_role_record_scope CHECK (record_scope IN ('all','own'))
)
""",
    "CREATE UNIQUE INDEX uq_app_role_code ON app_role (code)",
    "CREATE UNIQUE INDEX uq_app_role_name ON app_role (lower(name))",
    """
CREATE TABLE app_role_permission (
    role_id         uuid NOT NULL REFERENCES app_role(id) ON DELETE CASCADE,
    permission_code text NOT NULL REFERENCES app_permission(code)
                         ON UPDATE CASCADE ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_code)
)
""",
    "CREATE INDEX ix_app_role_permission_code ON app_role_permission (permission_code)",
    # фильтр области «процесс [+ только свои]» (доска, реестр, отчёты, экспорт, поиск)
    "CREATE INDEX ix_deal_pipeline_responsible ON deal (pipeline, responsible_user_id) "
    "WHERE status <> 'archived'",
)

_DROP = (
    "DROP INDEX IF EXISTS ix_deal_pipeline_responsible",
    "DROP TABLE IF EXISTS app_role_permission",
    "DROP TABLE IF EXISTS app_role",
    "DROP TABLE IF EXISTS app_permission",
)

# (code, group_code, name, description, is_scoped, sort_order)
_PERMISSIONS = (
    ('requests:read', 'requests', 'Просмотр заявок, доски и карточек', 'Реестр и доска заявок, карточка, история и комментарии', True, 10),
    ('requests:write', 'requests', 'Создание заявок и правка своих', 'Новые заявки и изменение полей заявок, где пользователь — ответственный', True, 20),
    ('requests:write_any', 'requests', 'Правка и переходы по чужим заявкам', 'Изменение полей и перевод по этапам заявок любого ответственного', True, 30),
    ('requests:transition', 'requests', 'Перевод заявок по этапам', 'Переходы своих заявок (чужих — вместе с «Правка и переходы по чужим заявкам»)', True, 40),
    ('requests:assign', 'requests', 'Назначение ответственного: взять заявку на себя', 'Взять себе свободную заявку (её ответственный не ведёт портфель как КАМ)', True, 50),
    ('requests:assign_any', 'requests', 'Назначение любого ответственного', 'Назначение и переназначение ответственного по любой заявке, в том числе при создании', True, 60),
    ('requests:kam', 'requests', 'Ведёт портфель заявок как КАМ (отчёт нагрузки, «свободные» заявки)', 'Пользователь попадает в отчёт нагрузки КАМов и в выбор КАМа на дашборде; его заявки не считаются свободными', True, 70),
    ('requests:reopen', 'requests', 'Переоткрытие закрытых заявок', 'Возврат заявки из терминального этапа в работу', True, 80),
    ('requests:delete', 'requests', 'Удаление заявок (в архив)', 'Мягкое удаление: заявка уходит в архив и пропадает из выдач', True, 90),
    ('comments:write', 'requests', 'Комментарии к заявкам', 'Добавление комментариев и удаление своих', True, 100),
    ('comments:delete_any', 'requests', 'Удаление чужих комментариев', 'Модерация переписки по заявке', True, 110),
    ('requests:files', 'requests', 'Файлы заявок: загрузка и удаление своих', 'Вложения карточки заявки; удаление чужих файлов — с правом «Правка и переходы по чужим заявкам»', True, 120),
    ('clients:pii', 'requests', 'Контакты клиентов B2C без маскирования', 'ФИО, email и телефон физлиц в карточке заявки показываются полностью', False, 130),
    ('workflow:read', 'workflow', 'Просмотр схемы процесса', 'Этапы, переходы и условия процесса в конструкторе', True, 140),
    ('workflow:history', 'workflow', 'История и пакеты изменений схемы', 'Журнал пакетов изменений и их статусы', True, 150),
    ('workflow:change', 'workflow', 'Правка схемы: черновик и пакет изменений', 'Черновик, оценка влияния и отправка пакета на согласование', True, 160),
    ('workflow:approve', 'workflow', 'Согласование и публикация изменений схемы', 'Согласование чужих пакетов; безопасный пакет автора применяется сразу', True, 170),
    ('universities:read', 'crm', 'Просмотр вузов и контактов', 'Реестр вузов, карточка вуза и контактные лица', False, 180),
    ('universities:write', 'crm', 'Создание и правка вузов и контактов', 'Свои вузы (КАМ — ответственный) и контакты; чужие — с «Правкой чужих вузов и договоров»', False, 190),
    ('universities:delete', 'crm', 'Удаление вузов', 'Деактивация вуза', False, 200),
    ('contacts:delete', 'crm', 'Удаление контактов вуза', 'Удаление контактных лиц из карточки вуза', False, 210),
    ('contracts:read', 'crm', 'Просмотр договоров', 'Реестр, карточка и файлы договоров', False, 220),
    ('contracts:write', 'crm', 'Создание и правка договоров, загрузка файлов', 'Свои договоры; чужие — с «Правкой чужих вузов и договоров»', False, 230),
    ('contracts:delete', 'crm', 'Удаление договоров', 'Расторжение (мягкое удаление) договора', False, 240),
    ('contracts:files_delete', 'crm', 'Удаление файлов договоров', 'Удаление вложений из карточки договора', False, 250),
    ('crm:write_any', 'crm', 'Правка чужих вузов и договоров (КАМ — другой)', 'Без этого права правка доступна только там, где пользователь — ответственный', False, 260),
    ('products:read', 'crm', 'Просмотр продуктов', 'Каталог продуктов', False, 270),
    ('products:write', 'crm', 'Создание и правка продуктов', 'Карточки продуктов каталога', False, 280),
    ('products:delete', 'crm', 'Удаление продуктов', 'Деактивация продукта', False, 290),
    ('programs:read', 'crm', 'Просмотр программ', 'Программы и их приоритеты', False, 300),
    ('programs:write', 'crm', 'Создание и правка программ', 'Карточки программ', False, 310),
    ('programs:priority', 'crm', 'Ручная приоритизация программ', 'Изменение порядка программ (меньше — выше)', False, 320),
    ('programs:delete', 'crm', 'Удаление программ', 'Деактивация программы', False, 330),
    ('files:business', 'crm', 'Скачивание файлов договоров, студентов и заявок', 'Без этого права файлы этих карточек не скачиваются', False, 340),
    ('students:read', 'talent', 'Просмотр студентов (ПДн маскированы)', 'Реестр и карточка студента, история воронки', False, 350),
    ('students:write', 'talent', 'Создание и правка студентов, смена статуса', 'Карточка студента и переходы по воронке талантов', False, 360),
    ('students:reveal', 'talent', 'Раскрытие ПДн студента (с записью в аудит)', 'Точечный просмотр ФИО и контактов; каждое раскрытие пишется в журнал аудита', False, 370),
    ('students:files', 'talent', 'Файлы студентов: просмотр и загрузка', 'Вложения карточки студента', False, 380),
    ('students:files_delete', 'talent', 'Удаление файлов студентов', 'Удаление вложений из карточки студента', False, 390),
    ('students:delete', 'talent', 'Удаление студентов', 'Удаление карточки студента', False, 400),
    ('activities:read', 'talent', 'Просмотр активностей и расписания', 'Сквозное расписание активностей', False, 410),
    ('activities:write', 'talent', 'Создание и правка активностей', 'Активности и их расписание', False, 420),
    ('activities:participants', 'talent', 'Запись студентов на активности', 'Добавление и удаление участников', False, 430),
    ('reports:read', 'reports', 'Дашборды и отчёты', 'Без «Отчётов по всем КАМам» — только по заявкам, где пользователь ответственный', True, 440),
    ('reports:all', 'reports', 'Отчёты по всем КАМам (иначе — только по своим заявкам)', 'Агрегаты по всем заявкам процессов роли', True, 450),
    ('export:run', 'reports', 'Экспорт данных (ПДн маскируются по правам)', 'Выгрузки XLSX/CSV/JSON реестров и отчётов в пределах области роли', False, 460),
    ('import:run', 'import', 'Мастер импорта данных', 'Загрузка файлов, маппинг, проверка и применение импорта', False, 470),
    ('import:dictionaries', 'import', 'Импорт справочников через мастер', 'Справочники в мастере импорта', False, 480),
    ('notifications:self', 'notifications', 'Личные уведомления и привязка Telegram', 'Лента, личные настройки каналов и событий, Telegram-бот', False, 490),
    ('notification_rules:read', 'notifications', 'Просмотр правил уведомлений и порогов', 'Правила адресации событий', False, 500),
    ('notification_rules:write', 'notifications', 'Настройка правил уведомлений', 'Создание, изменение и удаление правил', False, 510),
    ('admin:users', 'admin', 'Список пользователей', 'Пользователи и их роли', False, 520),
    ('admin:users_write', 'admin', 'Руководители для эскалаций, синхронизация с Keycloak', 'Иерархия руководителей и перенос пользователей из Keycloak', False, 530),
    ('admin:roles', 'admin', 'Роли и права, назначение ролей', 'Создание и правка ролей, назначение ролей пользователям', False, 540),
    ('admin:settings', 'admin', 'Системные настройки и пороги', 'Пороги зависания, эскалации, режим выдачи файлов', False, 550),
    ('admin:dictionaries', 'admin', 'Справочники', 'Справочники системы', False, 560),
    ('admin:flags', 'admin', 'Фичефлаги', 'Включение и выключение функций', False, 570),
    ('admin:audit', 'admin', 'Журнал аудита и интеграций (чтение)', 'Сквозной журнал действий и журнал интеграционных событий', False, 580),
    ('admin:integrations', 'admin', 'Повтор доставки интеграций', 'Ручной повтор события из очереди недоставленных', False, 590),
    ('files:others', 'admin', 'Импорты, выгрузки и служебные файлы других пользователей', 'Без этого права сессии импорта и выгрузки видит только их автор', False, 600),
)

# (code, name, description, is_locked); все системные: workflow_types={b2b,b2c}, record_scope=all
_ROLES = (
    ('admin', 'Администратор', 'Полный доступ. Права и область не редактируются — это защищает систему от потери управления', True),
    ('head_kam', 'Руководитель КАМов', 'Все заявки и справочники, распределение заявок, правка схемы процесса через согласование', False),
    ('kam', 'КАМ', 'Ведёт свой портфель заявок и вузов; видит все заявки, правит свои', False),
    ('observer', 'Наблюдатель', 'Только чтение: заявки, справочники, отчёты по всем КАМам, аудит; ПДн маскированы', False),
)

# наборы прав системных ролей по умолчанию; admin — все права, кроме requests:kam
# (администратор не ведёт портфель как КАМ и не попадает в отчёт нагрузки)
_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "admin": tuple(code for code, *_ in _PERMISSIONS if code != "requests:kam"),
    'head_kam': (
        'requests:read',
        'requests:write',
        'requests:write_any',
        'requests:transition',
        'requests:assign',
        'requests:assign_any',
        'comments:write',
        'requests:files',
        'clients:pii',
        'workflow:read',
        'workflow:history',
        'workflow:change',
        'universities:read',
        'universities:write',
        'contacts:delete',
        'contracts:read',
        'contracts:write',
        'contracts:files_delete',
        'crm:write_any',
        'products:read',
        'products:write',
        'programs:read',
        'programs:write',
        'programs:priority',
        'files:business',
        'students:read',
        'students:write',
        'students:reveal',
        'students:files',
        'students:files_delete',
        'activities:read',
        'activities:write',
        'activities:participants',
        'reports:read',
        'reports:all',
        'export:run',
        'import:run',
        'notifications:self',
        'notification_rules:read',
        'admin:users',
    ),
    'kam': (
        'requests:read',
        'requests:write',
        'requests:transition',
        'requests:assign',
        'requests:kam',
        'comments:write',
        'requests:files',
        'clients:pii',
        'workflow:read',
        'universities:read',
        'universities:write',
        'contracts:read',
        'contracts:write',
        'products:read',
        'programs:read',
        'programs:write',
        'programs:priority',
        'files:business',
        'students:read',
        'students:write',
        'students:reveal',
        'students:files',
        'activities:read',
        'activities:participants',
        'reports:read',
        'export:run',
        'import:run',
        'notifications:self',
    ),
    'observer': (
        'requests:read',
        'workflow:read',
        'workflow:history',
        'universities:read',
        'contracts:read',
        'products:read',
        'programs:read',
        'students:read',
        'activities:read',
        'reports:read',
        'reports:all',
        'export:run',
        'notifications:self',
        'admin:audit',
    ),
}


def upgrade() -> None:
    for statement in _DDL:
        op.execute(statement)

    permission_table = sa.table(
        "app_permission",
        sa.column("code", sa.Text),
        sa.column("group_code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("is_scoped", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "code": code,
                "group_code": group_code,
                "name": name,
                "description": description,
                "is_scoped": is_scoped,
                "sort_order": sort_order,
            }
            for code, group_code, name, description, is_scoped, sort_order in _PERMISSIONS
        ],
    )

    role_table = sa.table(
        "app_role",
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("is_system", sa.Boolean),
        sa.column("is_locked", sa.Boolean),
        sa.column("workflow_types", postgresql.ARRAY(sa.Text)),
        sa.column("record_scope", sa.Text),
    )
    op.bulk_insert(
        role_table,
        [
            {
                "code": code,
                "name": name,
                "description": description,
                "is_system": True,
                "is_locked": is_locked,
                "workflow_types": ["b2b", "b2c"],
                "record_scope": "all",
            }
            for code, name, description, is_locked in _ROLES
        ],
    )

    bind = op.get_bind()
    role_ids = dict(bind.execute(sa.text("SELECT code, id FROM app_role")).fetchall())
    link_table = sa.table(
        "app_role_permission",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_code", sa.Text),
    )
    op.bulk_insert(
        link_table,
        [
            {"role_id": role_ids[role_code], "permission_code": permission_code}
            for role_code, permissions in _ROLE_PERMISSIONS.items()
            for permission_code in permissions
        ],
    )

    # назначения — только коды из каталога ролей: сервисные роли и мусор из Keycloak
    # уходят, назначения системных ролей сохраняются как есть
    op.execute(
        "UPDATE app_user SET roles = ARRAY("
        "SELECT r FROM unnest(roles) r WHERE r IN (SELECT code FROM app_role) ORDER BY r) "
        "WHERE NOT roles <@ ARRAY(SELECT code FROM app_role)"
    )


def downgrade() -> None:
    for statement in _DROP:
        op.execute(statement)
