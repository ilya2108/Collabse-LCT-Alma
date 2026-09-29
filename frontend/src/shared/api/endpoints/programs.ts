import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type { ListEnvelope, Program } from '../types';

/**
 * Программы и ручное ранжирование приоритетов (api-contract.md §3.4):
 * `priority_rank` — меньше = выше (OVERVIEW.md Р-12), сортировка по умолчанию.
 */

export function listPrograms(params: RegistryFetchParams): Promise<ListEnvelope<Program>> {
  return api.get<ListEnvelope<Program>>('/programs', {
    query: {
      limit: params.limit,
      offset: params.offset,
      sort: params.sort,
      search: params.search,
      ...params.filters,
    },
    signal: params.signal,
  });
}

export function listProgramsByProduct(
  productId: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<Program>> {
  return api.get<ListEnvelope<Program>>('/programs', {
    query: { product_id: productId, limit: 200 },
    signal,
  });
}

export function getProgram(id: string, signal?: AbortSignal): Promise<Program> {
  return api.get<Program>(`/programs/${id}`, { signal });
}

export interface ProgramPayload {
  name: string;
  product_id?: string | null;
  university_id?: string | null;
  description?: string | null;
  seats?: number | null;
  starts_on?: string | null;
  published_to_cms?: boolean;
  is_active?: boolean;
}

export function createProgram(payload: ProgramPayload): Promise<Program> {
  return api.post<Program>('/programs', { body: payload });
}

export function updateProgram(
  id: string,
  patch: Partial<ProgramPayload> & { version: number },
): Promise<Program> {
  return api.patch<Program>(`/programs/${id}`, { body: patch });
}

/** Точечное изменение приоритета (▲/▼) — §3.4. */
export function updateProgramPriority(
  id: string,
  priorityRank: number,
  version: number,
): Promise<Program> {
  return api.patch<Program>(`/programs/${id}/priority`, {
    body: { priority_rank: priorityRank, version },
  });
}

export interface ReorderItem {
  id: string;
  priority_rank: number;
}

/** Массовое изменение приоритетов после drag&drop строк — §3.4. */
export function reorderProgramPriorities(items: ReorderItem[]): Promise<void> {
  return api.post<void>('/programs/priorities/reorder', { body: { items } });
}
