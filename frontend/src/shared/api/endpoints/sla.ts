import { api } from '../client';
import type { QueryValue } from '../client';
import type { ExportFormat, WorkflowType } from '../types';
import { runExportAndDownload } from './exportJobs';
import type {
  KamActivityEnvelopeParams,
  KamActivityParams,
  KamActivityScope,
} from './kamActivity';

/**
 * Вкладка «Просрочки» отчёта «Активность КАМов» — `/reports/sla` и
 * `/reports/sla/kams/{kam_id}` (api-contract.md §7.2.2). «Тикет» — визит заявки на
 * этапе; срок ему задаёт норма этапа (порог зависания: этап → процесс). Просрочено —
 * заявка на этапе дольше нормы, то же, что «зависшие» на других вкладках. Атрибуция —
 * на дату (кто вёл заявку в момент среза), а не по текущему ответственному.
 *
 * Типы — зеркало backend/app/modules/reporting/sla_schemas.py; живут здесь, а не в
 * types.ts, чтобы не пересекаться с другими доработками. В коде отчёт называется `sla`,
 * в интерфейсе и выгрузках — «Просрочки».
 */

/** Корзины задержки сверх нормы: 1–3, 4–7, 8–14, 15–30, больше 30 дней. */
export type SlaBucketKey = 'd1_3' | 'd4_7' | 'd8_14' | 'd15_30' | 'd31_plus';

/** Откуда норма этапа: задана для этапа, общая для процесса или глобальная. */
export type SlaNormSource = 'stage' | 'workflow' | 'global';

/** Дельта остатков — к срезу на начало периода, показателей за период — к прошлому периоду. */
export type SlaCompare = 'snapshot' | 'period';

export interface SlaKpiValue {
  value: number | null;
  prev: number | null;
  /** Проценты — в п.п., дни — с 1 знаком; null — нет базы для сравнения. */
  delta: number | null;
  compare: SlaCompare;
}

export type SlaKpiKey =
  | 'overdue'
  | 'active'
  | 'own_overdue'
  | 'inherited'
  | 'overdue_pct'
  | 'delay_median_days'
  | 'delay_avg_days'
  | 'delay_max_days'
  | 'delay_total_days'
  | 'overdue_30_plus'
  | 'overdue_no_action'
  | 'decided'
  | 'on_time'
  | 'on_time_pct'
  | 'completed'
  | 'late_closed'
  | 'late_delay_avg_days'
  | 'late_delay_median_days'
  | 'inflow';

/** Все 19 ключей приходят всегда (`KPI_COMPARE` в sla.py). */
export type SlaKpi = Record<SlaKpiKey, SlaKpiValue>;

export interface SlaPeriod {
  from: string;
  to: string;
  /** Момент среза T = min(конец периода, сейчас). */
  as_of: string;
  days: number;
  is_current: boolean;
}

export interface SlaPreviousPeriod {
  from: string;
  to: string;
  as_of: string;
  days: number;
}

export interface SlaComparison {
  /** «к 01.10» — для остатков (срез на начало периода). */
  snapshot_label: string;
  /** «к 15.09–30.09» — для показателей за период. */
  period_label: string;
}

export interface SlaAgingRow {
  key: SlaBucketKey;
  label: string;
  count: number;
  prev: number | null;
}

export interface SlaWorkflowSummary {
  /** «Вузы (B2B)» / «Клиенты (B2C)». */
  label: string;
  active: number;
  overdue: number;
  overdue_pct: number | null;
  delay_median_days: number | null;
  on_time_pct: number | null;
}

export interface SlaStageRef {
  id: string;
  code: string;
  /** Мягко удалённый этап — с пометкой «(удалён)». */
  name: string;
  color: string | null;
  deleted: boolean;
}

export interface SlaStageRow extends SlaStageRef {
  sort_order: number;
  norm_days: number;
  norm_source: SlaNormSource;
  active: number;
  overdue: number;
  overdue_pct: number | null;
  aging: Record<SlaBucketKey, number>;
  delay_median_days: number | null;
  delay_avg_days: number | null;
  delay_total_days: number | null;
  /** Решённые в периоде визиты: «уложились» + «не уложились». */
  decided: number;
  on_time: number;
  on_time_pct: number | null;
  completed: number;
  late_delay_avg_days: number | null;
}

export interface SlaTrendPoint {
  start: string;
  end: string;
  snapshot_at: string;
  /** Дата среза «23.08» (у месяцев — «на 30.09»). */
  label: string;
  /** false — корзина не закончилась (последняя точка, пунктир). */
  complete: boolean;
  in_period: boolean;
  active: number;
  overdue: number;
  /** null при active < min_n. */
  overdue_pct: number | null;
  overdue_30_plus: number;
  delay_avg_days: number | null;
  decided: number;
  on_time: number;
  /** null при decided < min_n. */
  on_time_pct: number | null;
}

export interface SlaTrend {
  granularity: 'week' | 'month';
  window: { from: string; to: string };
  min_n: number;
  points: SlaTrendPoint[];
}

export interface SlaReminderStats {
  visits_reminded: number;
  visits_escalated: number;
  sent: number;
  delivered: number;
  episodes: number;
  matured: number;
  reacted_72h: number;
  reacted_72h_pct: number | null;
  no_action: number;
  pending: number;
  undelivered: number;
  reaction_median_hours: number | null;
  reaction_avg_hours: number | null;
}

export type SlaCoverage = 'none' | 'partial' | 'full';

export interface SlaRemindersInfo {
  coverage: SlaCoverage;
  prev_coverage: SlaCoverage;
  journal_from: string | null;
  check_enabled: boolean;
  disabled_since: string | null;
  rules_active: boolean;
  note: string;
}

export interface SlaKamPrev {
  active: number;
  overdue: number;
  own_overdue: number;
  overdue_pct: number | null;
  on_time_pct: number | null;
  delay_avg_days: number | null;
  delay_median_days: number | null;
}

export interface SlaKamDelta {
  overdue: number | null;
  overdue_pct: number | null;
  on_time_pct: number | null;
  delay_avg_days: number | null;
  delay_median_days: number | null;
}

export interface SlaKamRow {
  kam_id: string;
  kam: string;
  is_active: boolean;
  roles: string[];
  active: number;
  overdue: number;
  own_overdue: number;
  /** Получены уже просроченными при передаче. */
  inherited: number;
  overdue_pct: number | null;
  /** В работе меньше 3 — доля показывается серым. */
  small_sample: boolean;
  delay_avg_days: number | null;
  delay_median_days: number | null;
  delay_max_days: number | null;
  delay_total_days: number | null;
  aging: Record<SlaBucketKey, number>;
  overdue_30_plus: number;
  overdue_no_action: number;
  decided: number;
  on_time: number;
  on_time_pct: number | null;
  completed: number;
  late_closed: number;
  late_inherited: number;
  late_delay_avg_days: number | null;
  late_delay_median_days: number | null;
  /** Поступило за период: созданные у КАМа и назначенные на него. */
  inflow: number;
  /** null — журнал напоминаний пуст. */
  reminders: SlaReminderStats | null;
  prev: SlaKamPrev | null;
  delta: SlaKamDelta | null;
  /** Попадает под правило «Просрочек больше, чем по команде, или старше 30 дней». */
  attention: boolean;
}

export interface SlaAttentionItem {
  kam_id: string;
  kam: string;
  active: number;
  overdue: number;
  own_overdue: number;
  inherited: number;
  overdue_pct: number | null;
  overdue_30_plus: number;
  /** До 6 строк-фактов, без оценок. */
  facts: string[];
}

export interface SlaAttention {
  /** Правило отбора — тот же текст в UI, XLSX и PDF. */
  rule: string;
  team_own_overdue_pct: number | null;
  threshold_pct: number | null;
  total_kams: number;
  items: SlaAttentionItem[];
}

/** Вуз заявки. */
export interface SlaUniversityRef {
  id: string;
  name: string;
  short_name: string | null;
}

/** B2C-клиент — отображаемое имя контрагента; у B2B-заявки — null. */
export type SlaClientRef = string | null;

export type SlaActionKind = 'transition' | 'created' | 'comment' | 'file' | (string & {});

export interface SlaLastAction {
  at: string;
  kind: SlaActionKind;
  actor_id: string | null;
  actor: string | null;
  days_ago: number;
}

export interface SlaOverdueItem {
  request_id: string;
  title: string;
  workflow_type: WorkflowType;
  university: SlaUniversityRef | null;
  client: SlaClientRef;
  kam_id: string;
  kam: string;
  /** Этап на момент среза. */
  stage: SlaStageRef;
  stage_since: string;
  on_stage_days: number;
  norm_days: number;
  norm_source: SlaNormSource;
  delay_days: number;
  bucket: SlaBucketKey;
  /** null — момент выхода за норму не восстановлен. */
  overdue_since: string | null;
  no_action_after_due: boolean;
  inherited: boolean;
  with_kam_since: string | null;
  reminders: number;
  delivered: number;
  escalated: boolean;
  last_action: SlaLastAction | null;
}

export interface SlaOverdueList {
  total: number;
  /** Список обрезан предохранителем — полный в XLSX. */
  truncated: boolean;
  items: SlaOverdueItem[];
}

export interface SlaTeamRef {
  kams_total: number;
  /** Медиан нет при «только свои» (тогда kams_total = 1). */
  active_median?: number | null;
  overdue_median?: number | null;
}

export interface SlaInactiveOwners {
  users: number;
  active: number;
  overdue: number;
}

/** Строка «Как считается» — общий текст для UI, XLSX и PDF. */
export interface SlaDefinition {
  term: string;
  text: string;
}

export interface SlaReport {
  report: 'sla';
  generated_at: string;
  params: KamActivityEnvelopeParams;
  period: SlaPeriod;
  previous_period: SlaPreviousPeriod;
  comparison: SlaComparison;
  scope: KamActivityScope;
  /** Детерминированная строка-итог — тот же текст в A1 XLSX и в PDF. */
  headline: string;
  definition: string;
  /** «Как считается» — тот же текст, что в XLSX и PDF. */
  definitions: SlaDefinition[];
  kpi: SlaKpi;
  team: SlaTeamRef;
  inactive_owners: SlaInactiveOwners | null;
  aging: SlaAgingRow[];
  by_workflow: Partial<Record<WorkflowType, SlaWorkflowSummary>>;
  stages: Partial<Record<WorkflowType, SlaStageRow[]>>;
  trend: SlaTrend;
  kams: SlaKamRow[];
  attention: SlaAttention;
  overdue: SlaOverdueList;
  reminders: {
    info: SlaRemindersInfo;
    /** null — журнал напоминаний пуст. */
    team: SlaReminderStats | null;
    /** Эпизоды — только в выгрузке. */
    items?: SlaReminderEpisode[];
  };
  /** Факты о данных: смена норм, переводы, передачи, журнал. */
  notes: string[];
  /** Опоздание закрытых этапов — только в выгрузке. */
  late_closed?: SlaLateClosedList;
}

export interface SlaKamTeamRef {
  overdue_pct: number | null;
  delay_median_days: number | null;
  delay_avg_days: number | null;
  on_time_pct: number | null;
  active_median: number | null;
  reacted_72h_pct: number | null;
  reaction_median_hours: number | null;
}

export interface SlaKamStageRow extends SlaStageRow {
  workflow_type: WorkflowType;
}

export interface SlaLateClosedItem {
  request_id: string;
  title: string;
  workflow_type?: WorkflowType;
  /** Кто вёл заявку при выходе с этапа (атрибуция на дату). */
  kam_id?: string;
  kam?: string;
  university: SlaUniversityRef | null;
  client: SlaClientRef;
  stage: SlaStageRef;
  entered: string;
  left_at: string;
  duration_days: number;
  norm_days: number;
  delay_days: number;
  inherited: boolean;
  exited_to: { name: string; is_return: boolean; outcome: 'won' | 'lost' | null };
}

export interface SlaLateClosedList {
  total: number;
  items: SlaLateClosedItem[];
}

export type SlaEpisodeStatus = 'reacted_72h' | 'no_action' | 'pending' | 'undelivered';

export interface SlaReminderEpisode {
  request_id: string;
  title: string;
  /** Получатель напоминания. */
  kam_id?: string;
  kam?: string;
  stage: SlaStageRef;
  first_reminder_at: string;
  delivered_at: string | null;
  status: SlaEpisodeStatus;
  reaction_at: string | null;
  reaction_kind: SlaActionKind | null;
  reaction_hours: number | null;
}

/** Счётчики, эпизоды и прошлый период есть, только если журнал напоминаний не пуст
 * (`info.coverage ≠ none`); иначе — только `info`. */
export type SlaKamReminders = Partial<SlaReminderStats> & {
  info: SlaRemindersInfo;
  items?: SlaReminderEpisode[];
  /** Прошлый период — для дельты (скрыта при prev_coverage ≠ full). */
  prev?: SlaReminderStats | null;
};

export interface SlaKamReport {
  report: 'sla-kam';
  generated_at: string;
  params: KamActivityEnvelopeParams;
  period: SlaPeriod;
  previous_period: SlaPreviousPeriod;
  comparison: SlaComparison;
  scope: KamActivityScope;
  kam: { id: string; name: string; is_active: boolean; roles: string[] };
  headline: string;
  definition: string;
  definitions: SlaDefinition[];
  kpi: SlaKpi;
  /** Справочные значения команды; null при «только свои». */
  team: SlaKamTeamRef | null;
  aging: SlaAgingRow[];
  overdue: SlaOverdueList;
  stages: SlaKamStageRow[];
  trend: SlaTrend;
  late_closed: SlaLateClosedList;
  reminders: SlaKamReminders;
  notes: string[];
}

/** Query общим проходом по ключам параметров отчёта КАМов: новые фильтры подхватятся сами. */
function toQuery(params: KamActivityParams): Record<string, QueryValue | QueryValue[]> {
  const query: Record<string, QueryValue | QueryValue[]> = {};
  for (const [key, value] of Object.entries(params) as Array<[string, unknown]>) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      if (value.length > 0) query[key] = value as QueryValue[];
    } else {
      query[key] = value as QueryValue;
    }
  }
  return query;
}

export function getSla(params: KamActivityParams, signal?: AbortSignal): Promise<SlaReport> {
  return api.get<SlaReport>('/reports/sla', { query: toQuery(params), signal });
}

/** Разбор КАМа; `kam_id` из параметров сервер игнорирует. */
export function getSlaKam(
  kamId: string,
  params: KamActivityParams,
  signal?: AbortSignal,
): Promise<SlaKamReport> {
  const { kam_id: _ignored, ...rest } = params;
  return api.get<SlaKamReport>(`/reports/sla/kams/${encodeURIComponent(kamId)}`, {
    query: toQuery(rest),
    signal,
  });
}

export type SlaExportFormat = Extract<ExportFormat, 'pdf' | 'xlsx' | 'csv'>;

export interface SlaExportOptions {
  /** PDF: false — «Сводка без имён», true — «По КАМам». */
  by_kam?: boolean;
}

/**
 * Серверная выгрузка `report:sla` через `/export/jobs`: XLSX — 8 листов, PDF — сводка
 * без имён или по КАМам, CSV — просроченные заявки. Файл — `overdue_{stamp}.{ext}`.
 */
export function exportSla(
  format: SlaExportFormat,
  params: KamActivityParams,
  options: SlaExportOptions = {},
): Promise<string> {
  return runExportAndDownload(
    {
      entity_type: 'report:sla',
      format,
      filters: toQuery(params) as Record<string, unknown>,
      options: format === 'pdf' ? { by_kam: options.by_kam ?? false } : {},
    },
    'overdue',
  );
}
