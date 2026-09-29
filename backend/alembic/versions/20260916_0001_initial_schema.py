"""Начальная схема: 31 таблица + расширение pg_trgm.

DDL дословно повторяет docs/design/data-model.md (источник правды по схеме).
CHECK-констрейнты и partial unique индексы заданы raw DDL — Alembic-автогенерация
их не покрывает.

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_DDL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- === 3. Пользователи (зеркало Keycloak) =============================================

CREATE TABLE app_user (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    keycloak_sub  uuid NOT NULL,
    username      text NOT NULL,
    email         text,
    full_name     text,
    roles         text[] NOT NULL DEFAULT '{}',
    manager_id    uuid REFERENCES app_user(id) ON DELETE SET NULL,
    tg_chat_id    bigint,
    tg_username   text,
    tg_linked_at  timestamptz,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_app_user_keycloak_sub ON app_user (keycloak_sub);
CREATE UNIQUE INDEX uq_app_user_username     ON app_user (lower(username));
CREATE UNIQUE INDEX uq_app_user_tg_chat_id   ON app_user (tg_chat_id) WHERE tg_chat_id IS NOT NULL;
CREATE INDEX ix_app_user_roles ON app_user USING gin (roles);

-- === 4.1 Вузы ========================================================================

CREATE TABLE university (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    short_name text,
    inn        text,
    kpp        text,
    region     text,
    city       text,
    website    text,
    kam_user_id uuid REFERENCES app_user(id),
    notes      text,
    is_active  boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_university_name ON university (lower(name));
CREATE INDEX ix_university_name_trgm ON university USING gin (name gin_trgm_ops);
CREATE INDEX ix_university_region ON university (region);

-- === 4.2 Контактные лица вуза ========================================================

CREATE TABLE university_contact (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    university_id uuid NOT NULL REFERENCES university(id) ON DELETE CASCADE,
    full_name     text NOT NULL,
    position      text,
    email         text,
    phone         text,
    is_primary    boolean NOT NULL DEFAULT false,
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_university_contact_university ON university_contact (university_id);

-- === 4.3 Контрагенты B2C (физлица и юрлица) ==========================================

CREATE TABLE counterparty (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind           text NOT NULL CHECK (kind IN ('person', 'legal_entity')),
    display_name   text NOT NULL,
    full_name_enc  bytea,
    email_enc      bytea,
    phone_enc      bytea,
    email_hmac     char(64),
    full_name_hmac char(64),
    org_name       text,
    inn            text,
    kpp            text,
    org_email      text,
    org_phone      text,
    notes          text,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_counterparty_kind_fields CHECK (
        (kind = 'person'       AND full_name_enc IS NOT NULL AND org_name IS NULL)
     OR (kind = 'legal_entity' AND org_name IS NOT NULL      AND full_name_enc IS NULL)
    )
);
CREATE INDEX ix_counterparty_kind ON counterparty (kind);
CREATE INDEX ix_counterparty_email_hmac ON counterparty (email_hmac) WHERE email_hmac IS NOT NULL;
CREATE INDEX ix_counterparty_inn ON counterparty (inn) WHERE inn IS NOT NULL;
CREATE INDEX ix_counterparty_display_trgm ON counterparty USING gin (display_name gin_trgm_ops);

-- === 4.4 Типы взаимодействия B2C ======================================================

CREATE TABLE interaction_type (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline    text NOT NULL DEFAULT 'b2c' CHECK (pipeline IN ('b2b', 'b2c')),
    code        text NOT NULL,
    name        text NOT NULL,
    description text,
    sort_order  int  NOT NULL DEFAULT 100,
    is_active   boolean NOT NULL DEFAULT true,
    created_by  uuid REFERENCES app_user(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_interaction_type_code ON interaction_type (pipeline, lower(code));

-- === 4.5 Продукты и программы ==========================================================

CREATE TABLE product (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL,
    name         text NOT NULL,
    product_type text NOT NULL DEFAULT 'course'
                 CHECK (product_type IN ('course', 'dpo_program', 'network_program', 'service', 'other')),
    description  text,
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_product_code ON product (lower(code));

CREATE TABLE program (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          uuid REFERENCES product(id) ON DELETE RESTRICT,
    university_id       uuid REFERENCES university(id) ON DELETE SET NULL,
    name                text NOT NULL,
    description         text,
    duration_hours      int CHECK (duration_hours IS NULL OR duration_hours > 0),
    seats               int CHECK (seats IS NULL OR seats > 0),
    starts_on           date,
    published_to_cms    boolean NOT NULL DEFAULT false,
    priority_rank       int NOT NULL DEFAULT 1000,
    priority_updated_by uuid REFERENCES app_user(id),
    priority_updated_at timestamptz,
    is_active           boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_program_product  ON program (product_id);
CREATE INDEX ix_program_priority ON program (priority_rank) WHERE is_active;

-- === 4.6 Договоры =======================================================================

CREATE TABLE contract (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    number              text NOT NULL,
    university_id       uuid REFERENCES university(id)   ON DELETE RESTRICT,
    counterparty_id     uuid REFERENCES counterparty(id) ON DELETE RESTRICT,
    status              text NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'negotiation', 'active', 'completed', 'terminated')),
    signed_at           date,
    valid_from          date,
    valid_to            date,
    amount              numeric(14, 2),
    currency            char(3) NOT NULL DEFAULT 'RUB',
    responsible_user_id uuid REFERENCES app_user(id),
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_contract_one_owner CHECK (num_nonnulls(university_id, counterparty_id) = 1),
    CONSTRAINT ck_contract_dates CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);
CREATE INDEX ix_contract_university   ON contract (university_id) WHERE university_id IS NOT NULL;
CREATE INDEX ix_contract_counterparty ON contract (counterparty_id) WHERE counterparty_id IS NOT NULL;
CREATE UNIQUE INDEX uq_contract_number ON contract (lower(number));

CREATE TABLE contract_product (
    contract_id uuid NOT NULL REFERENCES contract(id) ON DELETE CASCADE,
    product_id  uuid NOT NULL REFERENCES product(id)  ON DELETE RESTRICT,
    note        text,
    added_by    uuid REFERENCES app_user(id),
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (contract_id, product_id)
);
CREATE INDEX ix_contract_product_product ON contract_product (product_id);

-- === 5. Workflow как данные ==============================================================

CREATE TABLE workflow (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                 text NOT NULL,
    name                 text NOT NULL,
    description          text,
    stuck_threshold_days int NOT NULL DEFAULT 14 CHECK (stuck_threshold_days BETWEEN 1 AND 60),
    updated_by           uuid REFERENCES app_user(id),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_workflow_code ON workflow (code);

CREATE TABLE workflow_stage (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id          uuid NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    code                 text NOT NULL,
    name                 text NOT NULL,
    description          text,
    sort_order           int  NOT NULL DEFAULT 0,
    is_initial           boolean NOT NULL DEFAULT false,
    is_terminal          boolean NOT NULL DEFAULT false,
    terminal_outcome     text CHECK (terminal_outcome IN ('won', 'lost')),
    stuck_threshold_days int CHECK (stuck_threshold_days IS NULL OR stuck_threshold_days BETWEEN 1 AND 60),
    triggers_lms_handover boolean NOT NULL DEFAULT false,
    color                text,
    ui_position          jsonb,
    deleted_at           timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_stage_terminal_outcome CHECK (is_terminal OR terminal_outcome IS NULL)
);
CREATE UNIQUE INDEX uq_stage_workflow_code ON workflow_stage (workflow_id, lower(code))
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_stage_single_initial ON workflow_stage (workflow_id)
    WHERE is_initial AND deleted_at IS NULL;
CREATE INDEX ix_stage_workflow ON workflow_stage (workflow_id, sort_order) WHERE deleted_at IS NULL;

CREATE TABLE workflow_transition (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id   uuid NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    from_stage_id uuid NOT NULL REFERENCES workflow_stage(id),
    to_stage_id   uuid NOT NULL REFERENCES workflow_stage(id),
    name          text NOT NULL,
    is_return     boolean NOT NULL DEFAULT false,
    requires_comment boolean NOT NULL DEFAULT false,
    allowed_roles jsonb NOT NULL DEFAULT '[]',
    condition     jsonb,
    sort_order    int NOT NULL DEFAULT 0,
    deleted_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_transition_not_loop CHECK (from_stage_id <> to_stage_id),
    CONSTRAINT ck_transition_return_comment CHECK (NOT is_return OR requires_comment)
);
CREATE UNIQUE INDEX uq_transition_pair ON workflow_transition (from_stage_id, to_stage_id)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_transition_from ON workflow_transition (from_stage_id) WHERE deleted_at IS NULL;

CREATE TABLE workflow_change_request (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id      uuid NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    operations       jsonb NOT NULL,
    impact           jsonb,
    status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'failed')),
    requested_by     uuid NOT NULL REFERENCES app_user(id),
    requested_at     timestamptz NOT NULL DEFAULT now(),
    decided_by       uuid REFERENCES app_user(id),
    decided_at       timestamptz,
    decision_comment text,
    applied_at       timestamptz
);
CREATE INDEX ix_wcr_status ON workflow_change_request (status, requested_at) WHERE status = 'pending';
CREATE INDEX ix_wcr_workflow ON workflow_change_request (workflow_id, requested_at DESC);

-- === 6. Заявки ============================================================================

CREATE TABLE deal (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline            text NOT NULL CHECK (pipeline IN ('b2b', 'b2c')),
    workflow_id         uuid NOT NULL REFERENCES workflow(id),
    stage_id            uuid NOT NULL REFERENCES workflow_stage(id),
    title               text NOT NULL,
    university_id       uuid REFERENCES university(id),
    counterparty_id     uuid REFERENCES counterparty(id),
    interaction_type_id uuid REFERENCES interaction_type(id),
    contract_id         uuid REFERENCES contract(id),
    product_id          uuid REFERENCES product(id),
    program_id          uuid REFERENCES program(id),
    responsible_user_id uuid NOT NULL REFERENCES app_user(id),
    amount              numeric(14, 2),
    currency            char(3) NOT NULL DEFAULT 'RUB',
    status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'won', 'lost', 'archived')),
    stage_entered_at    timestamptz NOT NULL DEFAULT now(),
    last_activity_at    timestamptz NOT NULL DEFAULT now(),
    source              text NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'import', 'cms', 'lms')),
    description         text,
    created_by          uuid REFERENCES app_user(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_deal_client CHECK (
        (pipeline = 'b2b' AND university_id   IS NOT NULL)
     OR (pipeline = 'b2c' AND counterparty_id IS NOT NULL)
    )
);
CREATE INDEX ix_deal_board       ON deal (workflow_id, stage_id) WHERE status = 'open';
CREATE INDEX ix_deal_responsible ON deal (responsible_user_id)   WHERE status = 'open';
CREATE INDEX ix_deal_university  ON deal (university_id)  WHERE university_id  IS NOT NULL;
CREATE INDEX ix_deal_counterparty ON deal (counterparty_id) WHERE counterparty_id IS NOT NULL;
CREATE INDEX ix_deal_stuck       ON deal (stage_entered_at)      WHERE status = 'open';
CREATE INDEX ix_deal_created     ON deal (created_at DESC);

CREATE TABLE deal_stage_history (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id         uuid NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    from_stage_id   uuid REFERENCES workflow_stage(id),
    to_stage_id     uuid NOT NULL REFERENCES workflow_stage(id),
    from_stage_name text,
    to_stage_name   text NOT NULL,
    transition_id   uuid REFERENCES workflow_transition(id),
    is_return       boolean NOT NULL DEFAULT false,
    actor_user_id   uuid REFERENCES app_user(id),
    kind            text NOT NULL DEFAULT 'manual'
                    CHECK (kind IN ('manual', 'system_migration', 'integration', 'import')),
    reason          text,
    comment         text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_dsh_deal      ON deal_stage_history (deal_id, created_at);
CREATE INDEX ix_dsh_to_stage  ON deal_stage_history (to_stage_id, created_at);
CREATE INDEX ix_dsh_created   ON deal_stage_history (created_at);

CREATE TABLE deal_comment (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id        uuid NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    author_user_id uuid NOT NULL REFERENCES app_user(id),
    body           text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    edited_at      timestamptz,
    deleted_at     timestamptz
);
CREATE INDEX ix_deal_comment_deal ON deal_comment (deal_id, created_at);

-- === 9.2 Файлы (до import_session: FK file_id) ============================================

CREATE TABLE file_object (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    s3_bucket     text NOT NULL DEFAULT 'crm-files',
    s3_key        text NOT NULL,
    original_name text NOT NULL,
    content_type  text,
    size_bytes    bigint,
    sha256        char(64),
    entity_type   text CHECK (entity_type IN
                  ('deal', 'deal_comment', 'student', 'contract', 'university',
                   'import_session', 'report', 'other')),
    entity_id     uuid,
    uploaded_by   uuid REFERENCES app_user(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);
CREATE UNIQUE INDEX uq_file_object_s3_key ON file_object (s3_bucket, s3_key);
CREATE INDEX ix_file_object_entity ON file_object (entity_type, entity_id) WHERE deleted_at IS NULL;

-- === 9.1 Импорт ============================================================================

CREATE TABLE import_session (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type       text NOT NULL CHECK (entity_type IN
                      ('students', 'universities', 'university_contacts', 'counterparties',
                       'requests_b2b', 'requests_b2c', 'contracts', 'products', 'programs')
                      OR entity_type LIKE 'dictionary:%'),
    file_id           uuid REFERENCES file_object(id),
    original_filename text,
    file_format       text CHECK (file_format IN ('xlsx', 'xls', 'csv', 'json')),
    detected_encoding text,
    detected_columns  jsonb,
    sample_rows       jsonb,
    column_mapping    jsonb,
    options           jsonb NOT NULL DEFAULT '{}',
    status            text NOT NULL DEFAULT 'created' CHECK (status IN
                      ('created', 'uploaded', 'parsed', 'mapping', 'preview',
                       'applying', 'completed', 'failed', 'cancelled')),
    stats             jsonb,
    error_message     text,
    created_by        uuid NOT NULL REFERENCES app_user(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);
CREATE INDEX ix_import_session_author ON import_session (created_by, created_at DESC);
CREATE INDEX ix_import_session_status ON import_session (status)
    WHERE status NOT IN ('completed', 'cancelled', 'failed');

CREATE TABLE import_row_error (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    import_session_id uuid NOT NULL REFERENCES import_session(id) ON DELETE CASCADE,
    row_number        int NOT NULL,
    raw_data          jsonb,
    error             text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_import_row_error_session ON import_row_error (import_session_id, row_number);

-- === 7. Talent Pool ========================================================================

CREATE TABLE federal_project (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    code        text,
    description text,
    starts_on   date,
    ends_on     date,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_federal_project_name ON federal_project (lower(name));

CREATE TABLE student (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name_enc      bytea NOT NULL,
    email_enc          bytea NOT NULL,
    phone_enc          bytea,
    email_hmac         char(64) NOT NULL,
    full_name_hmac     char(64),
    display_name       text NOT NULL,
    university_id      uuid REFERENCES university(id),
    product_id         uuid REFERENCES product(id),
    program_id         uuid REFERENCES program(id),
    counterparty_id    uuid REFERENCES counterparty(id),
    federal_project_id uuid REFERENCES federal_project(id),
    funnel_status      text NOT NULL DEFAULT 'candidate'
                       CHECK (funnel_status IN ('candidate', 'studying', 'graduate', 'talent_pool')),
    status_changed_at  timestamptz NOT NULL DEFAULT now(),
    source             text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'import', 'lms')),
    import_session_id  uuid REFERENCES import_session(id) ON DELETE SET NULL,
    notes              text,
    created_by         uuid REFERENCES app_user(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_student_email_hmac ON student (email_hmac);
CREATE INDEX ix_student_funnel     ON student (funnel_status);
CREATE INDEX ix_student_university ON student (university_id) WHERE university_id IS NOT NULL;
CREATE INDEX ix_student_fedproject ON student (federal_project_id) WHERE federal_project_id IS NOT NULL;
CREATE INDEX ix_student_name_hmac  ON student (full_name_hmac) WHERE full_name_hmac IS NOT NULL;
CREATE INDEX ix_student_display_trgm ON student USING gin (display_name gin_trgm_ops);

CREATE TABLE student_status_history (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id    uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    from_status   text,
    to_status     text NOT NULL,
    actor_user_id uuid REFERENCES app_user(id),
    reason        text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_ssh_student ON student_status_history (student_id, created_at);

CREATE TABLE activity (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL,
    activity_type       text NOT NULL DEFAULT 'other'
                        CHECK (activity_type IN ('course', 'event', 'internship', 'hackathon', 'other')),
    federal_project_id  uuid REFERENCES federal_project(id) ON DELETE SET NULL,
    university_id       uuid REFERENCES university(id) ON DELETE SET NULL,
    program_id          uuid REFERENCES program(id)    ON DELETE SET NULL,
    responsible_user_id uuid REFERENCES app_user(id),
    description         text,
    is_active           boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_activity_fedproject ON activity (federal_project_id);

CREATE TABLE activity_schedule (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    activity_id uuid NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    starts_at   timestamptz NOT NULL,
    ends_at     timestamptz,
    location    text,
    format      text CHECK (format IN ('online', 'offline', 'hybrid')),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_schedule_interval CHECK (ends_at IS NULL OR ends_at > starts_at)
);
CREATE INDEX ix_schedule_activity ON activity_schedule (activity_id, starts_at);
CREATE INDEX ix_schedule_starts   ON activity_schedule (starts_at);

CREATE TABLE student_activity (
    student_id   uuid NOT NULL REFERENCES student(id)  ON DELETE CASCADE,
    activity_id  uuid NOT NULL REFERENCES activity(id) ON DELETE CASCADE,
    status       text NOT NULL DEFAULT 'registered'
                 CHECK (status IN ('registered', 'in_progress', 'completed', 'dropped')),
    enrolled_at  timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    PRIMARY KEY (student_id, activity_id)
);
CREATE INDEX ix_student_activity_activity ON student_activity (activity_id);

-- === 9.3 Интеграции ========================================================================

CREATE TABLE integration_event (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id       uuid NOT NULL DEFAULT gen_random_uuid(),
    direction      text NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    system         text NOT NULL CHECK (system IN ('lms', 'cms')),
    event_type     text NOT NULL,
    entity_type    text,
    entity_id      uuid,
    payload        jsonb NOT NULL,
    correlation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    status         text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'retrying', 'delivered', 'processed', 'dead', 'skipped')),
    attempts       int NOT NULL DEFAULT 0,
    next_retry_at  timestamptz,
    last_error     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    processed_at   timestamptz
);
CREATE UNIQUE INDEX uq_integration_event_event_id ON integration_event (event_id);
CREATE INDEX ix_integration_event_pending ON integration_event (next_retry_at, created_at)
    WHERE status IN ('pending', 'retrying');
CREATE INDEX ix_integration_event_entity  ON integration_event (entity_type, entity_id, created_at);

CREATE TABLE external_link (
    entity_type text NOT NULL,
    entity_id   uuid NOT NULL,
    system      text NOT NULL CHECK (system IN ('lms', 'cms')),
    external_id text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_id, system)
);
CREATE UNIQUE INDEX uq_external_link_reverse ON external_link (system, entity_type, external_id);

-- === 9.4 Нотификации и настройки ============================================================

CREATE TABLE notification_rule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      text NOT NULL CHECK (event_type IN
                    ('request.created', 'request.transitioned', 'request.assigned',
                     'request.stuck', 'request.comment_added',
                     'workflow.change_requested', 'workflow.changed',
                     'student.talent_pool_added', 'import.finished',
                     'integration.lead_received', 'integration.delivery_failed')),
    workflow_id     uuid REFERENCES workflow(id)       ON DELETE CASCADE,
    stage_id        uuid REFERENCES workflow_stage(id) ON DELETE CASCADE,
    channel         text NOT NULL CHECK (channel IN ('telegram', 'email', 'max')),
    recipient_type  text NOT NULL CHECK (recipient_type IN
                    ('responsible', 'manager_of_responsible', 'role', 'user',
                     'tg_username', 'email')),
    recipient_value text,
    params          jsonb NOT NULL DEFAULT '{}',
    is_active       boolean NOT NULL DEFAULT true,
    created_by      uuid REFERENCES app_user(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_rule_recipient_value CHECK (
        recipient_type IN ('responsible', 'manager_of_responsible') OR recipient_value IS NOT NULL)
);
CREATE INDEX ix_notification_rule_event ON notification_rule (event_type) WHERE is_active;

CREATE TABLE notification (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id           uuid REFERENCES notification_rule(id) ON DELETE SET NULL,
    event_type        text NOT NULL,
    entity_type       text,
    entity_id         uuid,
    recipient_user_id uuid REFERENCES app_user(id),
    channel           text NOT NULL CHECK (channel IN ('telegram', 'email', 'max')),
    address           text NOT NULL,
    subject           text,
    body              text NOT NULL,
    payload           jsonb,
    dedup_key         text,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    attempts          int NOT NULL DEFAULT 0,
    last_error        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    sent_at           timestamptz
);
CREATE UNIQUE INDEX uq_notification_dedup ON notification (dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX ix_notification_pending ON notification (created_at) WHERE status = 'pending';
CREATE INDEX ix_notification_entity  ON notification (entity_type, entity_id, created_at);

CREATE TABLE app_setting (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    description text,
    updated_by  uuid REFERENCES app_user(id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- === 9.5 Аудит ==============================================================================

CREATE TABLE audit_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_user_id uuid REFERENCES app_user(id),
    action        text NOT NULL,
    entity_type   text NOT NULL,
    entity_id     uuid,
    before        jsonb,
    after         jsonb,
    ip            inet,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_entity ON audit_log (entity_type, entity_id, created_at);
CREATE INDEX ix_audit_actor  ON audit_log (actor_user_id, created_at);
CREATE INDEX ix_audit_action ON audit_log (action, created_at);
"""

_DROP = """
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS app_setting;
DROP TABLE IF EXISTS notification;
DROP TABLE IF EXISTS notification_rule;
DROP TABLE IF EXISTS external_link;
DROP TABLE IF EXISTS integration_event;
DROP TABLE IF EXISTS student_activity;
DROP TABLE IF EXISTS activity_schedule;
DROP TABLE IF EXISTS activity;
DROP TABLE IF EXISTS student_status_history;
DROP TABLE IF EXISTS student;
DROP TABLE IF EXISTS federal_project;
DROP TABLE IF EXISTS import_row_error;
DROP TABLE IF EXISTS import_session;
DROP TABLE IF EXISTS file_object;
DROP TABLE IF EXISTS deal_comment;
DROP TABLE IF EXISTS deal_stage_history;
DROP TABLE IF EXISTS deal;
DROP TABLE IF EXISTS workflow_change_request;
DROP TABLE IF EXISTS workflow_transition;
DROP TABLE IF EXISTS workflow_stage;
DROP TABLE IF EXISTS workflow;
DROP TABLE IF EXISTS contract_product;
DROP TABLE IF EXISTS contract;
DROP TABLE IF EXISTS program;
DROP TABLE IF EXISTS product;
DROP TABLE IF EXISTS interaction_type;
DROP TABLE IF EXISTS counterparty;
DROP TABLE IF EXISTS university_contact;
DROP TABLE IF EXISTS university;
DROP TABLE IF EXISTS app_user;
"""


def _statements(script: str) -> list[str]:
    """Разбивает скрипт на отдельные statements: asyncpg не выполняет несколько
    команд в одном prepared statement. Точек с запятой внутри строковых литералов
    в этом DDL нет; фрагменты только из комментариев отбрасываются."""
    result: list[str] = []
    for part in script.split(";"):
        meaningful = [
            line
            for line in part.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        if meaningful:
            result.append(part.strip())
    return result


def upgrade() -> None:
    for statement in _statements(_DDL):
        op.execute(statement)


def downgrade() -> None:
    for statement in _statements(_DROP):
        op.execute(statement)
