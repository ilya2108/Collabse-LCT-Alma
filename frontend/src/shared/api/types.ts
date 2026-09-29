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

/** Профиль текущего пользователя — `GET /auth/me` (§2.1). */
export interface CurrentUser {
  id: string;
  username: string;
  full_name: string;
  email: string;
  /** Сырые роли из токена (могут включать технические — UI фильтрует через isAppRole). */
  roles: string[];
  manager_id: string | null;
  /** Вычисляются на backend по матрице §12; фронт ничего не хардкодит. */
  permissions: string[];
  telegram: {
    linked: boolean;
    tg_username?: string | null;
    linked_at?: string | null;
  } | null;
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
export interface Product {
  id: string;
  name: string;
  code: string;
  product_type?: string;
  description: string | null;
  is_active: boolean;
  version: number;
  created_at?: string;
  updated_at?: string;
}

/** Программа (§3.4): priority_rank — меньше = выше (OVERVIEW Р-12). */
export interface Program {
  id: string;
  product_id: string | null;
  university_id: string | null;
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
  roles: string[];
  manager_id: string | null;
  telegram_linked?: boolean;
  last_login_at?: string | null;
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

/** Отчёт миграции после approve (§5.4). */
export interface WorkflowMigrationReport {
  migrated_requests: number;
  by_status: { from: string; to: string; count: number }[];
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

export interface DashboardKpi {
  active: DashboardKpiValue;
  completed: DashboardKpiValue;
  /** Конверсия в successful-терминал за период, проценты 0–100. */
  conversion: DashboardKpiValue;
  stuck: DashboardKpiValue;
}

/** Строка нагрузки КАМа (§7.2 kam-workload): stacked-bar по этапам. */
export interface KamWorkloadRow {
  kam_id: string;
  kam_name: string;
  by_status: ReportSeriesItem[];
  stuck?: number;
}

/** Строка спроса программ (§7.2 programs-demand). */
export interface ProgramDemandRow {
  program_id: string;
  name: string;
  priority_rank: number;
  requests_count: number;
  /** «Расчётный спрос» — только при включённом фичефлаге auto_ranking. */
  computed_demand?: number | null;
}

/**
 * Агрегат всех виджетов дашборда — `GET /reports/dashboard` (§7.2):
 * секции в едином формате отчётов + блок KPI. Кэшируется на backend 60 с.
 */
export interface DashboardReport {
  report: string;
  generated_at: string;
  params?: Record<string, unknown>;
  kpi: DashboardKpi;
  funnel: { series: ReportSeriesItem[] };
  dynamics: { series: ReportDynamicsSeries[] };
  kam_workload: { rows: KamWorkloadRow[] };
  programs_demand: { rows: ProgramDemandRow[] };
  talent_pool_funnel: { series: ReportSeriesItem[] };
}

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
  integration_mode: 'http' | 'stream';
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
