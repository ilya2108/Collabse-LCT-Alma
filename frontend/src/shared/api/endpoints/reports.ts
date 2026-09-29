import { api } from '../client';
import type { QueryValue } from '../client';
import type { DashboardReport, TalentPoolFunnelReport } from '../types';

/**
 * JSON-агрегаты для дашбордов — `/reports/*` (api-contract.md §7.2).
 * Дашборд забирает все виджеты одним запросом `GET /reports/dashboard`
 * (backend кэширует агрегат в KeyDB 60 секунд); растровый экспорт PNG —
 * обязанность фронта (echartsInstance.getDataURL).
 */

export interface DashboardReportParams {
  workflow_type?: string;
  from?: string;
  to?: string;
  kam_id?: string[];
  university_id?: string[];
  product_id?: string[];
}

export function getDashboardReport(
  params: DashboardReportParams,
  signal?: AbortSignal,
): Promise<DashboardReport> {
  return api.get<DashboardReport>('/reports/dashboard', {
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
