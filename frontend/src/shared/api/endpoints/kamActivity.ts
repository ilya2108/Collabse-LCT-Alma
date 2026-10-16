import { api } from '../client';
import type { QueryValue } from '../client';
import type { AdminUser, ExportFormat, ListEnvelope, WorkflowType } from '../types';
import { runExportAndDownload } from './exportJobs';
import { listUsers } from './users';

/**
 * Отчёт «Активность КАМов» — `/reports/kam-activity` (api-contract.md §7.2.1):
 * какой КАМ за период с какими вузами взаимодействовал и на каких этапах
 * остановились заявки. Детализация (А) — руководителю КАМов, сводка (Б) —
 * бизнес-заказчику. Типы — зеркало backend/app/modules/reporting/schemas.py;
 * живут здесь, а не в types.ts, чтобы не пересекаться с другими доработками.
 */

export interface KamActivityParams {
  from?: string;
  to?: string;
  workflow_type?: WorkflowType;
  kam_id?: string[];
  university_id?: string[];
}

/** Собственные действия ответственного КАМа в периоде; total = П + К + Ф + М. */
export interface KamActivityInteractions {
  transitions: number;
  /** Подмножество переходов — возвраты на предыдущий этап. */
  returns: number;
  comments: number;
  files: number;
  activities: number;
  total: number;
}

export interface KamActivityStageChip {
  id: string;
  code: string;
  /** Удалённый этап — историческое название с пометкой «(удалён)». */
  name: string;
  color: string | null;
  is_terminal: boolean;
  outcome: 'won' | 'lost' | null;
  workflow_type: WorkflowType;
  count: number;
}

export interface KamActivityRequestStage {
  id: string;
  code: string;
  name: string;
  color: string | null;
  is_terminal: boolean;
  outcome: 'won' | 'lost' | null;
  deleted: boolean;
  sort_order: number;
  workflow_type: WorkflowType;
}

export interface KamActivityRequest {
  request_id: string;
  title: string;
  workflow_type: WorkflowType;
  /** Псевдоним B2C-клиента «Фамилия И.О.». */
  client: string | null;
  amount: string | null;
  status: 'open' | 'won' | 'lost' | 'archived';
  created_at: string;
  created_in_period: boolean;
  closed_in_period: 'won' | 'lost' | null;
  /** Этап на конец периода (восстановлен по истории переходов). */
  stage: KamActivityRequestStage;
  stage_since: string;
  days_on_stage: number;
  threshold_days: number;
  is_stuck: boolean;
  interactions: KamActivityInteractions;
  last_contact_at: string | null;
}

export interface KamActivityUniversity {
  /** null — узел «B2C-клиенты (без вуза)». */
  university_id: string | null;
  name: string;
  short_name: string | null;
  region: string | null;
  assigned: boolean;
  requests_count: number;
  new_requests: number;
  won: number;
  lost: number;
  stuck: number;
  interactions: KamActivityInteractions;
  last_contact_at: string | null;
  stages: KamActivityStageChip[];
  /** Заявки опущены предохранителем размера — догрузить по kam_id + university_id. */
  requests_truncated: boolean;
  requests: KamActivityRequest[];
}

export interface KamActivityKam {
  kam_id: string;
  kam: string;
  is_active: boolean;
  roles: string[];
  universities_count: number;
  universities_touched: number;
  universities_assigned: number;
  assigned_touched: number;
  assigned_untouched: number;
  coverage_pct: number | null;
  requests_count: number;
  new_requests: number;
  new_amount: string | null;
  won: number;
  lost: number;
  conversion: number | null;
  stuck: number;
  interactions: KamActivityInteractions;
  last_contact_at: string | null;
  stages: KamActivityStageChip[];
  universities: KamActivityUniversity[];
}

export interface KamActivityTotals {
  kams: number;
  universities_count: number;
  universities_touched: number;
  universities_assigned: number;
  assigned_touched: number;
  coverage_pct: number | null;
  requests_count: number;
  new_requests: number;
  new_amount: string | null;
  won: number;
  lost: number;
  conversion: number | null;
  stuck: number;
  interactions: KamActivityInteractions;
}

export interface KamActivityPeriod {
  from: string;
  to: string;
  /** «Данные на»: min(конец периода, сейчас). */
  as_of: string;
  days: number;
  granularity?: 'day' | 'week' | 'month';
}

export interface KamActivityScope {
  /** Отчёт только по своим заявкам (нет права «все КАМы»). */
  own_only: boolean;
  own_user_id: string | null;
  workflow_types: WorkflowType[];
}

export interface KamActivityEnvelopeParams {
  from: string | null;
  to: string | null;
  workflow_type: WorkflowType | null;
  kam_id: string[];
  university_id: string[];
}

export interface KamActivityReport {
  report: 'kam-activity';
  generated_at: string;
  params: KamActivityEnvelopeParams;
  period: KamActivityPeriod;
  scope: KamActivityScope;
  totals: KamActivityTotals;
  rows: KamActivityKam[];
}

export interface KpiValue<T = number | null> {
  value: T;
  prev: T;
  /** Разница к прошлому периоду той же длины; проценты — в п.п.; null — нет базы. */
  delta: T;
}

export interface KamActivityKpi {
  coverage_pct: KpiValue;
  universities_touched: KpiValue;
  universities_assigned: KpiValue;
  assigned_touched: KpiValue;
  requests_count: KpiValue;
  new_requests: KpiValue;
  new_amount: KpiValue<string | null>;
  won: KpiValue;
  lost: KpiValue;
  conversion: KpiValue;
  interactions: KpiValue;
  stuck: KpiValue;
  talent_pool_added: KpiValue;
}

export interface StageConversionRow {
  id: string;
  code: string;
  name: string;
  color: string | null;
  is_terminal: boolean;
  outcome: 'won' | 'lost' | null;
  deleted: boolean;
  /** Заявок дошло до этапа (по истории, до as_of). */
  reached: number;
  share_of_first_pct: number | null;
  step_conversion_pct: number | null;
  on_stage_at_end: number;
  stuck: number;
  avg_days: number | null;
  intervals: number;
  threshold_days: number | null;
}

export type KamActivitySeriesKey =
  | 'transitions'
  | 'comments'
  | 'files'
  | 'activities'
  | 'new_requests';

export interface KamActivityDynamicsSeries {
  key: KamActivitySeriesKey;
  label: string;
  points: Array<{ date: string; value: number }>;
}

export interface KamBreakdownRow {
  kam_id: string;
  kam: string;
  is_active: boolean;
  universities_touched: number;
  universities_assigned: number;
  assigned_touched: number;
  coverage_pct: number | null;
  requests_count: number;
  new_requests: number;
  won: number;
  lost: number;
  conversion: number | null;
  interactions: number;
  interactions_prev: number;
  interactions_detail: KamActivityInteractions;
  stuck: number;
  last_contact_at: string | null;
}

export interface StuckRequestRow {
  request_id: string;
  title: string;
  workflow_type: WorkflowType;
  university: string | null;
  kam_id: string;
  kam: string;
  stage: Omit<KamActivityRequestStage, 'deleted' | 'sort_order' | 'workflow_type'>;
  days_on_stage: number;
  threshold_days: number;
  overdue_days: number;
}

export type TalentStatusKey = 'candidate' | 'studying' | 'graduate' | 'talent_pool';

export interface KamActivityTalent {
  students_total: number;
  on_programs: number;
  by_status: Array<{
    key: TalentStatusKey;
    label: string;
    color: string;
    /** На конец периода (по истории статусов). */
    value: number;
    /** Перешли в статус за период. */
    entered: number;
  }>;
  talent_pool_added: KpiValue<number>;
  programs: Array<{
    program_id: string;
    name: string | null;
    university: string | null;
    candidate: number;
    studying: number;
    graduate: number;
    talent_pool: number;
    total: number;
  }>;
}

export interface KamActivitySummary {
  report: 'kam-activity-summary';
  generated_at: string;
  params: KamActivityEnvelopeParams;
  period: KamActivityPeriod;
  previous_period: Omit<KamActivityPeriod, 'granularity'>;
  scope: KamActivityScope;
  kpi: KamActivityKpi;
  /** ≤5 детерминированных выводов — тот же текст, что в PDF. */
  highlights: string[];
  stages: Partial<Record<WorkflowType, StageConversionRow[]>>;
  dynamics: { granularity: 'day' | 'week' | 'month'; series: KamActivityDynamicsSeries[] };
  by_kam: KamBreakdownRow[];
  top_stuck: StuckRequestRow[];
  untouched_assigned: string[];
  talent: KamActivityTalent;
}

function toQuery(params: KamActivityParams): Record<string, QueryValue | QueryValue[]> {
  return {
    from: params.from,
    to: params.to,
    workflow_type: params.workflow_type,
    kam_id: params.kam_id,
    university_id: params.university_id,
  };
}

export function getKamActivity(
  params: KamActivityParams,
  signal?: AbortSignal,
): Promise<KamActivityReport> {
  return api.get<KamActivityReport>('/reports/kam-activity', { query: toQuery(params), signal });
}

export function getKamActivitySummary(
  params: KamActivityParams,
  signal?: AbortSignal,
): Promise<KamActivitySummary> {
  return api.get<KamActivitySummary>('/reports/kam-activity/summary', {
    query: toQuery(params),
    signal,
  });
}

/** КАМы для фильтра: пользователи с правом «Ведёт портфель заявок как КАМ». */
export function listKamOptions(signal?: AbortSignal): Promise<ListEnvelope<AdminUser>> {
  return listUsers({ permission: 'requests:kam', signal });
}

export type KamActivityExportFormat = Extract<ExportFormat, 'pdf' | 'xlsx' | 'csv'>;

/**
 * Серверная выгрузка через `/export/jobs` (журнал выгрузок, аудит, та же зона
 * видимости): PDF для заказчика, XLSX — все листы, CSV — список заявок.
 */
export function exportKamActivity(
  format: KamActivityExportFormat,
  params: KamActivityParams,
): Promise<string> {
  return runExportAndDownload(
    {
      entity_type: 'report:kam-activity',
      format,
      filters: toQuery(params) as Record<string, unknown>,
    },
    'kam-activity',
  );
}
