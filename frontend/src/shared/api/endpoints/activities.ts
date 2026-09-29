import { api } from '../client';
import type { Activity, ListEnvelope } from '../types';

/**
 * Сквозное расписание активностей — `GET /activities` (api-contract.md §8.1):
 * фильтры from/to/kind/federal_project. Читают все роли; CRUD — admin/head_kam
 * (в UI стадии MVP — только просмотр календаря-списка).
 */

export interface ActivityFilters {
  from?: string;
  to?: string;
  kind?: string;
  federal_project?: string;
}

export function listActivities(
  filters: ActivityFilters = {},
  signal?: AbortSignal,
): Promise<ListEnvelope<Activity>> {
  return api.get<ListEnvelope<Activity>>('/activities', {
    query: { limit: 200, ...filters },
    signal,
  });
}
