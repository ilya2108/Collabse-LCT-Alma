/** Общие типы контракта API (docs/design/api-contract.md). */

/** Ответ-конверт списка — везде одинаковый (§1.5). */
export interface ListEnvelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Элемент `details` тела ошибки (§1.4). */
export interface ApiErrorDetail {
  field?: string;
  code: string;
  message: string;
}

/** Единый формат ошибки `{error: {...}}` (§1.4). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: ApiErrorDetail[];
    trace_id?: string;
  };
}

/** Область записей роли в процессе: все заявки или только свои (Р-F2-4). */
export type RecordScope = 'all' | 'own';

/** Ссылка на роль в профиле (`role_details` в `/auth/me`). */
export interface RoleRef {
  code: string;
  name: string;
  is_system: boolean;
}

/**
 * Эффективный доступ пользователя (`/auth/me` → `access`, F2 §4.7): права ролей
 * объединяются по процессу — в процессе P действуют права ролей, у которых P
 * входит в область.
 */
export interface AccessInfo {
  /** Процессы, доступные пользователю. */
  workflow_types: WorkflowType[];
  /** Область записей по процессу. */
  records: Partial<Record<WorkflowType, RecordScope>>;
  /** Процесс → права с областью, действующие в нём. */
  by_workflow: Partial<Record<WorkflowType, string[]>>;
  /** Процесс → коды ролей пользователя, способные выполнять переходы в нём. */
  transition_roles: Partial<Record<WorkflowType, string[]>>;
  /** Коды прав с областью (действуют только в процессах роли). */
  scoped_permissions: string[];
  /** Версия каталога ролей на сервере в момент ответа. */
  catalog_version?: number;
}

/** Профиль текущего пользователя — `GET /auth/me` (§2.1). */
export interface CurrentUser {
  id: string;
  username: string;
  full_name: string;
  email: string;
  /** Коды ролей CRM — назначаются в Альме (Р-F2-1), Keycloak отвечает только за вход. */
  roles: string[];
  /** Роли с названиями из каталога (своё название у пользовательских ролей). */
  role_details: RoleRef[];
  manager_id: string | null;
  /** Объединение прав всех ролей; фронт ничего не хардкодит. */
  permissions: string[];
  access: AccessInfo;
  telegram: {
    linked: boolean;
    tg_username?: string | null;
    linked_at?: string | null;
  } | null;
}

// ---------------------------------------------------------------------------
// Роли и права (F2: `/roles`, `/admin/roles`, `/admin/permissions`)
// ---------------------------------------------------------------------------

/** Элемент каталога ролей `GET /roles` — доступен всем аутентифицированным. */
export interface RoleCatalogItem {
  code: string;
  name: string;
  description: string | null;
  is_system: boolean;
  workflow_types: WorkflowType[];
  record_scope: RecordScope;
  /** Процессы, где роль может выполнять переходы (право requests:transition). */
  transition_workflow_types: WorkflowType[];
}

/** Право каталога `GET /admin/permissions`. */
export interface PermissionDef {
  code: string;
  name: string;
  description: string | null;
  /** Действует только в процессах роли (B2B/B2C). */
  scoped: boolean;
  /** Права, без которых это право не имеет смысла (ставятся автоматически). */
  requires: string[];
}

/** Группа прав в порядке UI. */
export interface PermissionGroup {
  code: string;
  name: string;
  permissions: PermissionDef[];
}

/** Роль в админке `GET /admin/roles`. */
export interface AdminRole {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_system: boolean;
  /** admin: права и область не редактируются (Р-F2-9). */
  is_locked: boolean;
  workflow_types: WorkflowType[];
  record_scope: RecordScope;
  permissions: string[];
  users_count: number;
  permissions_count: number;
  updated_at: string;
  version: number;
}

/** Покрытие переходов процесса ролью: из `restricted` переходов с ограничением по ролям роль указана в `allowed`. */
export interface RoleTransitionCoverage {
  restricted: number;
  allowed: number;
}

/** Карточка роли `GET /admin/roles/{id}`. */
export interface AdminRoleDetail extends AdminRole {
  users: Array<{ id: string; full_name: string; username: string; is_active?: boolean }>;
  coverage: Partial<Record<WorkflowType, RoleTransitionCoverage>>;
  /** Только для системных ролей: права отличаются от набора по умолчанию. */
  differs_from_default?: boolean;
}

/** Тело `POST /admin/roles`. */
export interface RoleCreateBody {
  code: string;
  name: string;
  description?: string | null;
  permissions: string[];
  workflow_types: WorkflowType[];
  record_scope: RecordScope;
}

/** Тело `PATCH /admin/roles/{id}`: поля опциональны, `version` обязателен. */
export interface RolePatchBody {
  name?: string;
  description?: string | null;
  permissions?: string[];
  workflow_types?: WorkflowType[];
  record_scope?: RecordScope;
  version: number;
}

/** Пресет таблицы/фильтров — `/ui/presets` (§2.2). */
export interface UiPresetState {
  filters?: Record<string, unknown>;
  sort?: { field: string; order: 'asc' | 'desc' } | null;
  columns?: string[];
  page_size?: number;
}

export interface UiPreset {
  id: string;
  screen: string;
  name: string;
  is_default: boolean;
  state: UiPresetState;
}

/** Вуз (§3.1). */
export interface University {
  id: string;
  name: string;
  short_name: string | null;
  inn: string | null;
  kpp: string | null;
  region: string | null;
  city: string | null;
  website: string | null;
  kam_user_id: string | null;
  notes: string | null;
  is_active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
  /** Агрегаты карточки (§3.1: «+агрегаты: счётчики договоров/заявок/студентов»). */
  contracts_count?: number;
  active_requests?: number;
  students_count?: number;
}

/** Контактное лицо вуза (§3.1). */
export interface UniversityContact {
  id: string;
  full_name: string;
  position: string | null;
  email: string | null;
  phone: string | null;
  notes?: string | null;
  version?: number;
}

/** Статус договора (§3.2). */
export type ContractStatus = 'draft' | 'negotiation' | 'active' | 'completed' | 'terminated';

/** Статус передачи лицензии вузу; null — не указан. */
export type LicenseTransferStatus = 'not_started' | 'in_progress' | 'transferred' | 'cancelled';

/** Строка договора как лицензия на ИТ-продукт (§3.2). */
export interface ContractLicense {
  product_id: string;
  license_signed_at: string | null;
  license_term_years: number | null;
  /** Дата окончания — включительно. */
  license_valid_to: string | null;
  transfer_status: LicenseTransferStatus | null;
  transfer_status_updated_at?: string | null;
  note: string | null;
}

/** Договор (§3.2). */
export interface Contract {
  id: string;
  university_id: string | null;
  counterparty_id: string | null;
  number: string;
  status: ContractStatus;
  signed_at: string | null;
  valid_from: string | null;
  valid_to: string | null;
  amount: string | null;
  currency: string;
  product_ids: string[];
  /** Лицензии по продуктам договора (строки contract_product, импорт «Таблица КАМов»). */
  licenses?: ContractLicense[];
  notes?: string | null;
  version: number;
  created_at?: string;
  updated_at?: string;
  /** Денормализованные подписи владельца (если backend их отдаёт). */
  university?: { id: string; name: string } | null;
  counterparty?: { id: string; display_name: string } | null;
  /** Предупреждение о втором активном договоре вуза (§3.2). */
  warning_duplicate?: boolean;
}

/** Файл в MinIO — общий формат файлов всего API (§3.2). */
export interface FileObject {
  id: string;
  file_name: string;
  content_type: string | null;
  size_bytes: number;
  s3_key?: string;
  uploaded_by?: string | null;
  uploaded_by_name?: string | null;
  created_at: string;
}

/** Продукт (§3.3). */
/**
 * ИТ-направление обучения (DevOps, QA, анализ данных…) — справочник `it_direction`.
 * Читают все роли, правит право «Справочники» (`admin:dictionaries`).
 */
export interface ItDirection {
  id: string;
  code: string;
  name: string;
  description: string | null;
  sort_order: number;
  is_active: boolean;
  version: number;
  /** Активные программы с этим направлением. */
  programs_count: number;
  /** ИТ-продукты с этим направлением. */
  products_count: number;
  created_at?: string;
  updated_at?: string;
}

/** Направление в карточках программ и продуктов. */
export interface DirectionBrief {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
}

export interface Product {
  id: string;
  name: string;
  code: string;
  product_type?: string;
  description: string | null;
  is_active: boolean;
  /** ИТ-направления, которым помогает учить продукт (M:N). */
  direction_ids?: string[];
  directions?: DirectionBrief[];
  version: number;
  created_at?: string;
  updated_at?: string;
}

/** Программа (§3.4): priority_rank — меньше = выше (OVERVIEW Р-12). */
export interface Program {
  id: string;
  product_id: string | null;
  university_id: string | null;
  /** ИТ-направление программы (одно). */
  direction_id?: string | null;
  direction?: DirectionBrief | null;
  /** Направления ИТ-продукта: если своё не задано, программа относится к ним. */
  product_directions?: DirectionBrief[];
  name: string;
  description?: string | null;
  priority_rank: number;
  seats: number | null;
  starts_on: string | null;
  is_active: boolean;
  published_to_cms: boolean;
  cms_external_id: string | null;
  lms_course_id: string | null;
  version: number;
  created_at?: string;
  updated_at?: string;
}

/** Ссылка на пользователя в телах API (§4.2). */
export interface UserRef {
  id: string;
  full_name: string;
}

/** Ссылка на статус (этап) в телах заявки/истории (§4.2–4.3). */
export interface StatusRef {
  id: string;
  code: string;
  name: string;
  color?: string;
}

export type WorkflowType = 'b2b' | 'b2c';

/** B2C-контрагент в сериализации заявки (§4.2). */
export interface RequestClient {
  full_name: string | null;
  email: string | null;
  phone: string | null;
  company_name: string | null;
  inn: string | null;
}

/** Заявка (§4.2). В БД — deal, в UI — «заявка» (мост OVERVIEW §2.1). */
export interface RequestItem {
  id: string;
  workflow_type: WorkflowType;
  title: string;
  university_id: string | null;
  client_kind: 'person' | 'company' | null;
  counterparty_id: string | null;
  interaction_type_id: string | null;
  client: RequestClient | null;
  contract_id: string | null;
  product_id: string | null;
  program_id: string | null;
  status: StatusRef;
  assignee: UserRef | null;
  source: 'manual' | 'import' | 'cms' | 'lms';
  external_refs: { cms_lead_id: string | null; lms_enrollment_id: string | null } | null;
  amount: string | null;
  status_updated_at: string;
  is_stuck: boolean;
  stuck_days: number | null;
  description: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  /** Денормализованные подписи связей (если backend их отдаёт). */
  university?: { id: string; name: string } | null;
  product?: { id: string; name: string } | null;
  program?: { id: string; name: string } | null;
}

/** Элемент истории заявки (§4.3). */
export interface RequestHistoryItem {
  id: string;
  kind: 'transition' | 'assign' | 'field_change' | 'created' | 'migrated';
  from_status: StatusRef | null;
  to_status: StatusRef | null;
  is_return: boolean;
  comment: string | null;
  actor: UserRef | null;
  created_at: string;
  /** Служебная причина: 'stage_deleted', 'reopen', 'lms_status_sync', … */
  reason?: string | null;
}

/** Комментарий заявки (§4.4). Мягко удалённый приходит с текстом «(удалено)». */
export interface RequestComment {
  id: string;
  text: string;
  author: UserRef;
  created_at: string;
  deleted_at?: string | null;
}

/** Доступный переход для текущего пользователя (workflow-engine.md §7). */
export interface AvailableTransition {
  transition_id: string;
  to_status_id: string;
  name: string;
  kind: 'forward' | 'return';
  requires_comment: boolean;
  /** Аннотация condition-подсказки: false — не рекомендуется, но не блокируется. */
  available: boolean;
  unavailable_reason?: string | null;
  to_status?: StatusRef;
}

/** Координаты узла конструктора React Flow (data-model.md §5.1: jsonb ui_position). */
export interface UiPosition {
  x: number;
  y: number;
}

/** Статус (этап) схемы workflow (§5.1). */
export interface WorkflowStatus {
  id: string;
  code: string;
  name: string;
  color: string;
  is_initial: boolean;
  is_terminal: boolean;
  terminal_outcome: 'won' | 'lost' | null;
  stuck_threshold_days: number | null;
  triggers_lms_handover: boolean;
  position: number;
  /** Позиция узла в конструкторе; null/отсутствует — авторазмещение на клиенте. */
  ui_position?: UiPosition | null;
}

/** Переход схемы workflow (§5.1). */
export interface WorkflowTransition {
  id: string;
  from_status_id: string;
  to_status_id: string;
  kind: 'forward' | 'return';
  name: string;
  requires_comment: boolean;
  allowed_roles: string[];
  /** JSON-DSL подсказки ветвления (workflow-engine.md §3.3) — UI не редактирует, но сохраняет. */
  condition?: unknown;
}

/** Полная схема workflow (§5.1) — источник колонок канбана и графа конструктора. */
export interface WorkflowSchema {
  id: string;
  type: WorkflowType;
  name: string;
  updated_at: string;
  statuses: WorkflowStatus[];
  transitions: WorkflowTransition[];
}

/** Элемент списка `GET /workflows` (§5.2). */
export interface WorkflowListItem {
  id: string;
  type: WorkflowType;
  name: string;
  statuses_count: number;
  active_requests: number;
  updated_at: string;
}

/** Тип взаимодействия B2C — редактируемый справочник (data-model.md §4.4). */
export interface InteractionType {
  id: string;
  pipeline: WorkflowType;
  code: string;
  name: string;
  description?: string | null;
  sort_order?: number;
  is_active: boolean;
}

/** Пользователь в админ-списке `GET /admin/users` (§10.2). */
export interface AdminUser {
  id: string;
  username: string;
  full_name: string;
  email: string;
  /** Коды ролей CRM (только известные каталогу). */
  roles: string[];
  /** Названия ролей в том же порядке, что `roles`. */
  role_names?: string[];
  /** Ролей нет — вход в CRM закрыт, пока администратор не назначит роль. */
  no_roles?: boolean;
  manager_id: string | null;
  telegram_linked?: boolean;
  is_active?: boolean;
  last_login_at?: string | null;
  /** Версия записи — для `PUT /admin/users/{id}/roles`. */
  version?: number;
}

/** Запись журнала интеграций `GET /admin/integration/events` (§10.4, data-model §9.3). */
export interface IntegrationEventRecord {
  id: string;
  event_id: string;
  direction: 'outbound' | 'inbound';
  system: 'lms' | 'cms';
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  payload: unknown;
  status: 'pending' | 'retrying' | 'delivered' | 'processed' | 'dead' | 'skipped';
  attempts: number;
  next_retry_at: string | null;
  last_error: string | null;
  created_at: string;
  processed_at: string | null;
}

// ---------------------------------------------------------------------------
// Изменения схемы workflow (§5.3–5.4, data-model.md §5.2)
// ---------------------------------------------------------------------------

/** Тело нового этапа в операции `add_status` (§5.3). */
export interface WorkflowStatusBody {
  code: string;
  name: string;
  color: string;
  is_initial?: boolean;
  is_terminal?: boolean;
  terminal_outcome?: 'won' | 'lost' | null;
  stuck_threshold_days?: number | null;
  triggers_lms_handover?: boolean;
  position: number;
  ui_position?: UiPosition | null;
}

/**
 * Тело нового перехода в операции `add_transition` (§5.3): этапы адресуются
 * кодами (`from_code`/`to_code`), потому что у добавляемых в этом же пакете
 * этапов ещё нет id.
 */
export interface WorkflowTransitionBody {
  from_code: string;
  to_code: string;
  kind: 'forward' | 'return';
  name: string;
  requires_comment?: boolean;
  allowed_roles?: string[];
}

/** Пакет операций `POST /workflows/{id}/changes` (§5.3). */
export type WorkflowOperation =
  | { op: 'add_status'; status: WorkflowStatusBody }
  | { op: 'rename_status'; status_id: string; new_name: string; confirm_name: string }
  | { op: 'update_status'; status_id: string; patch: Partial<Omit<WorkflowStatusBody, 'code'>> }
  | { op: 'delete_status'; status_id: string; migrate_to_status_id: string; confirm_name: string }
  | { op: 'add_transition'; transition: WorkflowTransitionBody }
  | { op: 'update_transition'; transition_id: string; patch: Partial<Omit<WorkflowTransitionBody, 'from_code' | 'to_code'>> }
  | { op: 'delete_transition'; transition_id: string };

/** Блок impact: сколько заявок будет перенесено (§5.3, impact-preview). */
export interface WorkflowChangeImpact {
  affected_requests_total: number;
  by_status: {
    status_id: string;
    status_name?: string;
    count: number;
    migrate_to?: { id: string; name: string } | null;
  }[];
}

/** Замеры применения пакета, мс (§5.4, workflow-engine.md §6.8). */
export interface WorkflowMigrationTimings {
  /** Ожидание эксклюзивного лока воронки — хвоста уже начатых переходов. */
  lock_wait_ms: number;
  /** От захвата лока до COMMIT: схема, перенос, история, сводки, аудит. */
  apply_ms: number;
  /** Из них — пакетный перенос заявок. */
  migrate_ms: number;
  /** Лок удерживался (включая COMMIT) — только в ответе approve. */
  lock_held_ms?: number | null;
}

/**
 * Отчёт миграции (§5.3–5.4): в ответе approve и у применённого пакета
 * (`WorkflowChangeRecord.migration_report`, хранится в `impact.migration_report`).
 */
export interface WorkflowMigrationReport {
  migrated_requests: number;
  by_status: { from: string; to: string; count: number }[];
  /** Сводных уведомлений `request.transitioned` — одно на получателя и канал. */
  digest_notifications?: number | null;
  /** Нет у пакетов, применённых до появления замеров. */
  timings?: WorkflowMigrationTimings | null;
}

export type WorkflowChangeStatus = 'pending' | 'approved' | 'rejected' | 'applied' | 'failed';

/** Пакет изменений схемы — `workflow_change_request` (§5.3–5.4, data-model.md §5.2). */
export interface WorkflowChangeRecord {
  id: string;
  workflow_id: string;
  status: WorkflowChangeStatus;
  author?: UserRef | null;
  comment?: string | null;
  operations: WorkflowOperation[];
  impact?: WorkflowChangeImpact | null;
  created_at: string;
  decided_by?: UserRef | null;
  decided_at?: string | null;
  decision_comment?: string | null;
  applied_at?: string | null;
  approved_by?: UserRef | null;
  migration_report?: WorkflowMigrationReport | null;
}

// ---------------------------------------------------------------------------
// Импорт (§6): сессия, маппинг, валидация
// ---------------------------------------------------------------------------

/** Целевая сущность импорта (§6.1). `dictionary:<name>` — только admin. */
export type ImportEntityType =
  | 'universities'
  | 'university_contacts'
  | 'contracts'
  | 'programs'
  | 'requests_b2b'
  | 'requests_b2c'
  | 'students'
  | 'kam_catalog'
  | (string & {});

/** Машина состояний `import_session` (data-model.md §9.1, ответы §6.2–6.4). */
export type ImportSessionState =
  | 'mapping'
  | 'validated'
  | 'applying'
  | 'completed'
  | 'canceled'
  | (string & {});

export interface ImportSheet {
  index: number;
  name: string;
  rows: number;
}

export interface ImportColumn {
  index: number;
  name: string;
  samples: string[];
}

/** Распознанная структура файла (§6.2). */
export interface ImportDetected {
  encoding: string | null;
  encoding_confidence?: number | null;
  sheets?: ImportSheet[];
  active_sheet?: number;
  header_row?: number | null;
  columns: ImportColumn[];
  /** Первые строки «как распарсили» для превью шага 2 (может отсутствовать — UI деградирует до samples). */
  preview_rows?: string[][];
}

export interface ImportTargetField {
  field: string;
  label: string;
  required: boolean;
  /** Справочник, по которому ищется значение (вуз, продукт, КАМ…); null — простое поле. */
  lookup?: string | null;
}

export interface ImportSuggestedMapping {
  source_column: number;
  target_field: string;
  confidence?: number;
}

/** Строка маппинга `PUT /import/sessions/{id}/mapping` (§6.3). */
export interface ImportMappingEntry {
  source_column: number;
  target_field: string;
  transform?: string;
  lookup?: string;
}

export type ImportUpdateStrategy = 'create_only' | 'upsert' | 'update_only';

export interface ImportMappingOptions {
  sheet?: number;
  header_row?: number | null;
  encoding?: string | null;
  update_strategy?: ImportUpdateStrategy;
  match_by?: string[];
  skip_empty_rows?: boolean;
  default_values?: Record<string, unknown>;
}

export interface ImportValidationError {
  row: number;
  column: string | null;
  code: string;
  message: string;
  /** Уровень строки отчёта; отсутствует — считаем ошибкой. */
  level?: 'error' | 'warning';
}

/** Ответ `POST /import/sessions/{id}/validate` (§6.4). */
export interface ImportValidationReport {
  state: ImportSessionState;
  total_rows: number;
  valid_rows: number;
  error_rows: number;
  errors: ImportValidationError[];
  errors_truncated?: boolean;
  error_report_file_id?: string | null;
}

/** Итог применения (§6.4). */
export interface ImportApplyResult {
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  report_file_id?: string | null;
  /** Разбивка составного импорта (kam_catalog): ключ счётчика → количество. */
  details?: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Talent Pool (§8, data-model.md §7)
// ---------------------------------------------------------------------------

/** Воронка студента — ровно 4 статуса (data-model.md §7.2). */
export type StudentFunnelStatus = 'candidate' | 'studying' | 'graduate' | 'talent_pool';

/** Участие студента в активности (student_activity, data-model.md §7.3). */
export type StudentActivityStatus = 'registered' | 'in_progress' | 'completed' | 'dropped';

/**
 * Активность студента в его карточке (§8.1: GET /students/{id} — «карточка +
 * файлы + активности + история»). Поля расписания опциональны — UI деградирует
 * до плоского списка, если backend их не отдаёт.
 */
export interface StudentActivityItem {
  id: string;
  name: string;
  activity_type?: string | null;
  status?: StudentActivityStatus | null;
  starts_at?: string | null;
  ends_at?: string | null;
  format?: 'online' | 'offline' | 'hybrid' | null;
  location?: string | null;
  federal_project?: { id: string; name: string } | null;
}

/** Студент (§8.2). `full_name`/`email` приходят ВСЕГДА маскированными. */
export interface Student {
  id: string;
  display_name: string;
  full_name: string;
  email: string;
  phone?: string | null;
  university_id: string | null;
  product_id?: string | null;
  program_id: string | null;
  federal_project_id: string | null;
  counterparty_id: string | null;
  /** Производное: последняя B2C-заявка контрагента (§8.2). */
  b2c_request_id: string | null;
  funnel_status: StudentFunnelStatus;
  source?: 'manual' | 'import' | 'lms';
  notes?: string | null;
  files_count?: number;
  version: number;
  created_at: string;
  updated_at: string;
  /** Денормализованные подписи связей (если backend их отдаёт). */
  university?: { id: string; name: string } | null;
  program?: { id: string; name: string } | null;
  federal_project?: { id: string; name: string } | null;
  /** Активности студента — только в карточке GET /students/{id}. */
  activities?: StudentActivityItem[];
}

/** Открытые ПДн из `POST /students/{id}/reveal` (§8.1) — живут в UI 60 секунд. */
export interface StudentRevealResult {
  full_name: string;
  email: string;
  phone?: string | null;
}

/** Элемент истории воронки — `GET /students/{id}/history` (§8.1). */
export interface StudentHistoryItem {
  id: string;
  from_status: StudentFunnelStatus | null;
  to_status: StudentFunnelStatus;
  actor: UserRef | null;
  reason: string | null;
  created_at: string;
}

/** Слот сквозного расписания активности (activity_schedule, data-model.md §7.3). */
export interface ActivityScheduleEntry {
  id: string;
  starts_at: string;
  ends_at: string | null;
  location: string | null;
  format: 'online' | 'offline' | 'hybrid' | null;
  note?: string | null;
}

/** Активность (`GET /activities`, §8.1): курс, мероприятие, стажировка… */
export interface Activity {
  id: string;
  name: string;
  activity_type: 'course' | 'event' | 'internship' | 'hackathon' | 'other';
  federal_project_id: string | null;
  university_id: string | null;
  program_id: string | null;
  description?: string | null;
  is_active: boolean;
  /** Слоты расписания (сквозной календарь). */
  schedule?: ActivityScheduleEntry[];
  participants_count?: number;
  federal_project?: { id: string; name: string } | null;
  university?: { id: string; name: string } | null;
}

// ---------------------------------------------------------------------------
// Отчёты и экспорт (§7)
// ---------------------------------------------------------------------------

/** Элемент series единого формата отчёта (§7.2). */
export interface ReportSeriesItem {
  key: string;
  label: string;
  color?: string | null;
  value: number;
  amount?: string | null;
}

export interface ReportDynamicsPoint {
  date: string;
  value: number;
}

/** Серия отчёта `dynamics` (§7.2): точки по неделям. */
export interface ReportDynamicsSeries {
  key: string;
  label: string;
  color?: string | null;
  points: ReportDynamicsPoint[];
}

/** KPI-значение с дельтой к прошлому периоду (ux.md §6.1, виджет 1). */
export interface DashboardKpiValue {
  value: number;
  /** Изменение к прошлому периоду (той же длины); null/отсутствует — нет данных. */
  delta?: number | null;
}

/* Типы ответа `GET /reports/dashboard` (KPI, нагрузка КАМов, востребованность программ) —
 * в `endpoints/reports.ts` рядом с запросом. */

/** Отчёт `GET /reports/talent-pool-funnel` (§7.2) — счётчики воронки студентов. */
export interface TalentPoolFunnelReport {
  report: string;
  generated_at: string;
  series: ReportSeriesItem[];
}

/** Форматы экспорта (§7.1); `pdf` — только за фичефлагом `report_pdf` (вне MVP). */
export type ExportFormat = 'xlsx' | 'csv' | 'json' | 'pdf';

export type ExportJobStatus = 'pending' | 'running' | 'done' | 'failed';

/** Задание экспорта (§7.1): ≤10 000 строк — синхронно `done`, иначе поллинг. */
export interface ExportJob {
  id: string;
  entity_type: string;
  format: ExportFormat;
  status: ExportJobStatus;
  file?: FileObject | null;
  error?: string | null;
  created_at?: string;
}

// ---------------------------------------------------------------------------
// Нотификации (§9)
// ---------------------------------------------------------------------------

export type NotificationChannelCode = 'in_app' | 'telegram' | 'email' | 'max';

/** Справочник каналов и их состояния — `GET /notifications/channels` (§9.1). */
export interface NotificationChannelInfo {
  code: NotificationChannelCode;
  name: string;
  /** `active` — рабочий канал, `stub` — заглушка, `down` — сервис недоступен. */
  status: 'active' | 'stub' | 'down';
}

export interface NotificationChannelSetting {
  enabled: boolean;
  linked?: boolean;
  address?: string | null;
}

export interface NotificationEventSetting {
  enabled: boolean;
  /** Только события по моим заявкам (есть не у всех типов событий). */
  only_mine?: boolean;
}

/** Настройки нотификаций пользователя — `GET/PUT /notifications/settings` (§9.1). */
export interface NotificationSettings {
  channels: Partial<Record<NotificationChannelCode, NotificationChannelSetting>>;
  events: Record<string, NotificationEventSetting>;
  version: number;
}

/** Одноразовый код привязки Telegram — `POST /notifications/telegram/link-code` (§9.2). */
export interface TelegramLinkCode {
  code: string;
  deep_link: string;
  expires_at: string;
}

/** Элемент in-app ленты — `GET /notifications` (§9.1, колокольчик ux.md §3.3). */
export interface NotificationFeedItem {
  id: string;
  event: string;
  /** Готовый русский текст уведомления. */
  text: string;
  /** Ссылка внутри приложения (например, /requests/{id}). */
  url?: string | null;
  is_read: boolean;
  created_at: string;
  payload?: Record<string, unknown> | null;
}

// ---------------------------------------------------------------------------
// Админка (§10) и фичефлаги (§2.2)
// ---------------------------------------------------------------------------

/** Карта активных флагов — `GET /flags` (§2.2). */
export type FlagsMap = Record<string, boolean>;

/** Флаг в админке — `GET /admin/feature-flags` (§2.2). */
export interface FeatureFlag {
  name: string;
  enabled: boolean;
  description?: string | null;
  updated_by?: UserRef | null;
  updated_at?: string | null;
}

/** Справочник в админке — `GET /admin/dictionaries` (§10.1). */
export interface DictionaryInfo {
  name: string;
  label: string;
  items_count: number;
  updated_at?: string | null;
}

/** Запись справочника (§10.1) — состав полей зависит от справочника. */
export interface DictionaryItem {
  id: string;
  name: string;
  code?: string | null;
  [key: string]: unknown;
}

/** Итог JSON-загрузки справочника — `POST /admin/dictionaries/{name}/import` (§10.1). */
export interface DictionaryImportResult {
  created: number;
  updated: number;
  failed: number;
  errors: { index?: number; message: string }[];
}

/** Системные настройки — `GET/PUT /admin/settings` (§10.3). */
export interface AdminSettings {
  stuck_threshold_days: number;
  stuck_escalate_to_manager: boolean;
  /** Единственный транспорт — outbox PostgreSQL → HTTP + HMAC (§13.7). */
  integration_mode: 'http';
  file_delivery: 'proxy' | 'presigned';
  version: number;
}

/** Запись журнала аудита — `GET /admin/audit-log` (§10.4, data-model.md §9.5). */
export interface AuditLogRecord {
  id: number | string;
  actor: UserRef | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before?: unknown;
  after?: unknown;
  ip: string | null;
  created_at: string;
}

/** Сессия импорта (§6.1–6.4): создаётся POST'ом файла, живёт 24 часа. */
export interface ImportSession {
  id: string;
  entity_type: ImportEntityType;
  state: ImportSessionState;
  file?: { file_name: string; format?: string; size_bytes?: number };
  detected?: ImportDetected;
  target_fields?: ImportTargetField[];
  suggested_mapping?: ImportSuggestedMapping[];
  /** Сохранённый маппинг — для восстановления диалога после F5 (GET §6.1). */
  mapping?: ImportMappingEntry[] | null;
  options?: ImportMappingOptions | null;
  validation?: ImportValidationReport | null;
  progress?: { done: number; total: number } | null;
  result?: ImportApplyResult | null;
  applied_at?: string | null;
  created_at?: string;
}
