import { api } from '../client';
import type { WorkflowListItem, WorkflowSchema } from '../types';

/** Схемы workflow (api-contract.md §5.2) — источник колонок канбана и графа. */

/**
 * §5.2 описывает ответ как массив `[{id, type, name, …}]`; терпимо принимаем
 * и конверт `{items: […]}` — нормализуем к массиву на клиенте.
 */
export async function listWorkflows(signal?: AbortSignal): Promise<WorkflowListItem[]> {
  const data = await api.get<WorkflowListItem[] | { items: WorkflowListItem[] }>('/workflows', {
    signal,
  });
  return Array.isArray(data) ? data : data.items;
}

export function getWorkflow(id: string, signal?: AbortSignal): Promise<WorkflowSchema> {
  return api.get<WorkflowSchema>(`/workflows/${id}`, { signal });
}
