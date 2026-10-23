import { api } from '../client';
import type { QueryValue } from '../client';
import type {
  DashboardKpiValue,
  ReportDynamicsSeries,
  ReportSeriesItem,
  TalentPoolFunnelReport,
} from '../types';

/**
 * JSON-агрегаты для дашбордов — `/reports/*` (api-contract.md §7.2).
 * Дашборд забирает все виджеты одним запросом `GET /reports/dashboard`
 * (backend кэширует агрегат в KeyDB 60 секунд); растровый экспорт PNG/PDF —
 * обязанность фронта (echartsInstance.getDataURL).
 *
 * Фильтры — списки id: массив уходит повтором параметра (`kam_id=a&kam_id=b`),
 * backend применяет `IN` во всех секциях (KPI, этапы, динамика, нагрузка,
 * программы, студенты); ИТ-направление — по программе и продукту заявки.
 */

export interface DashboardReportParams {
  workflow_type?: string;
  from?: string;
  to?: string;
  kam_id?: string[];
  university_id?: string[];
  product_id?: string[];
  program_id?: string[];
  /** ИТ-направления: направление программы заявки, без него — её ИТ-продукта. */
  direction_id?: string[];
}

export type WorkflowCode = 'b2b' | 'b2c';

/** Этап секции «Заявки по текущему этапу»: при «Все» — этапы всех процессов подряд. */
export interface DashboardStageItem extends ReportSeriesItem {
  status_id: string;
  workflow_type: WorkflowCode;
  /** Название этапа без префикса процесса (`label` при нескольких процессах — «B2B · …»). */
  stage_label: string;
  sort_order: number;
  is_terminal: boolean;
  /** Исход терминального этапа: «Отказ» показывается отдельно от последовательности. */
  outcome: 'won' | 'lost' | null;
}

/** Этап в строке нагрузки КАМа: `{code,count}` и тот же элемент в формате series. */
export interface KamWorkloadStatus {
  status_id: string;
  code: string;
  key: string;
  label: string;
  color?: string | null;
  count: number;
  value: number;
  sort_order: number;
  workflow_type: WorkflowCode;
}

export interface KamWorkloadItem {
  kam_id: string;
  kam_name: string;
  active_requests: number;
  universities: number;
  stuck: number;
  by_status: KamWorkloadStatus[];
  top_universities: { id: string; name: string; count: number }[];
}

/** Востребованность программы: три показателя ТЗ и ручной приоритет. */
export interface ProgramDemandItem {
  program_id: string;
  name: string;
  product_id: string | null;
  priority_rank: number;
  requests_count: number;
  open_requests: number;
  students_studying: number;
  parallel_streams: number;
  /** Индекс 0–100 — только при фичефлаге auto_ranking. */
  computed_demand: number | null;
}

export interface ReportPeriod {
  from: string | null;
  to: string | null;
}

export interface DashboardKpiPeriod {
  from: string;
  to: string;
  days: number;
  /** true — период не выбран, взяты последние 30 дней. */
  is_default: boolean;
  previous: { from: string; to: string };
}

export interface DashboardKpiBlock {
  /** На текущую дату. */
  active: DashboardKpiValue;
  /** Вход в «Завершена» (won) за период. */
  completed: DashboardKpiValue;
  /** Вход в «Отказ» (lost) за период. */
  lost: DashboardKpiValue;
  /** won / (won + lost) за период, 0–100. */
  conversion: DashboardKpiValue;
  /** На текущую дату. */
  stuck: DashboardKpiValue;
  period: DashboardKpiPeriod;
}

export interface DashboardData {
  report: string;
  generated_at: string;
  params?: Record<string, unknown>;
  kpi: DashboardKpiBlock;
  funnel: { series: DashboardStageItem[]; workflow_types: WorkflowCode[] };
  dynamics: { series: ReportDynamicsSeries[]; period: { from: string; to: string } };
  kam_workload: { rows: KamWorkloadItem[] };
  programs_demand: {
    rows: ProgramDemandItem[];
    auto_ranking: boolean;
    formula: string | null;
    period: ReportPeriod | null;
  };
  talent_pool_funnel: { series: ReportSeriesItem[] };
}

export interface ProgramsDemandReport {
  report: string;
  generated_at: string;
  rows: ProgramDemandItem[];
  columns: { key: string; label: string }[];
  auto_ranking: boolean;
  formula: string | null;
  period: ReportPeriod | null;
}

export function getDashboardReport(
  params: DashboardReportParams,
  signal?: AbortSignal,
): Promise<DashboardData> {
  return api.get<DashboardData>('/reports/dashboard', {
    query: params as Record<string, QueryValue | QueryValue[]>,
    signal,
  });
}

/** Число на текущий момент и разбивка по процессам панели (для перехода на доску). */
export interface SignalCount {
  value: number;
  by_workflow: Partial<Record<WorkflowCode, number>>;
}

export interface LicenseSignalItem {
  contract_id: string;
  contract_number: string;
  university_id: string;
  /** Краткое наименование вуза, без него — полное. */
  university: string;
  kam_id: string | null;
  kam: string | null;
  product_id: string;
  /** ИТ-продукт строки договора. */
  product: string;
  /** Последний день действия лицензии (включительно), ГГГГ-ММ-ДД. */
  valid_to: string;
  /** Дней до последнего дня: 0 — сегодня, отрицательное — истекла. */
  days_left: number;
  state: 'expiring' | 'expired';
  transfer_status: 'not_started' | 'in_progress' | 'transferred' | null;
}

export interface LicenseSignal {
  expiring: number;
  expired: number;
  horizon_days: number;
  items: LicenseSignalItem[];
  truncated: boolean;
}

export interface UntouchedUniversityItem {
  university_id: string;
  name: string;
  kam_id: string;
  kam: string;
  /** Последнее взаимодействие до периода; null — не было. */
  last_contact_at: string | null;
}

export interface UntouchedSignal {
  value: number;
  period: { from: string; to: string; as_of: string; days: number };
  items: UntouchedUniversityItem[];
  truncated: boolean;
}

/**
 * Карточка «Сигналы» (`GET /reports/signals`): срез на текущий момент, период панели не
 * применяется. Лицензии и вузы — только при процессе B2B в области роли, иначе null.
 */
export interface DashboardSignals {
  report: string;
  generated_at: string;
  as_of: string;
  workflow_types: WorkflowCode[];
  stuck: SignalCount;
  unassigned: SignalCount;
  licenses: LicenseSignal | null;
  untouched_universities: UntouchedSignal | null;
}

export function getDashboardSignals(
  params: DashboardReportParams,
  signal?: AbortSignal,
): Promise<DashboardSignals> {
  return api.get<DashboardSignals>('/reports/signals', {
    query: params as Record<string, QueryValue | QueryValue[]>,
    signal,
  });
}

export function getProgramsDemand(
  params: DashboardReportParams,
  signal?: AbortSignal,
): Promise<ProgramsDemandReport> {
  return api.get<ProgramsDemandReport>('/reports/programs-demand', {
    query: params as Record<string, QueryValue | QueryValue[]>,
    signal,
  });
}

export function getTalentPoolFunnel(
  params: { university_id?: string; federal_project?: string } = {},
  signal?: AbortSignal,
): Promise<TalentPoolFunnelReport> {
  return api.get<TalentPoolFunnelReport>('/reports/talent-pool-funnel', {
    query: params,
    signal,
  });
}
