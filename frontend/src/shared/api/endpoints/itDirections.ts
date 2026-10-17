import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type { ItDirection, ListEnvelope } from '../types';

/**
 * Справочник ИТ-направлений (`/it-directions`): читают все роли (фильтры отчётов,
 * карточки программ и продуктов), создаёт, правит и деактивирует право «Справочники».
 * Направление записи — направление её программы, без него — направления её ИТ-продукта.
 */

export function listItDirections(
  params: RegistryFetchParams,
): Promise<ListEnvelope<ItDirection>> {
  return api.get<ListEnvelope<ItDirection>>('/it-directions', {
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

/** Все направления для селектов и фильтров (их десяток); `activeOnly` — без деактивированных. */
export function listAllItDirections(
  signal?: AbortSignal,
  activeOnly = false,
): Promise<ListEnvelope<ItDirection>> {
  return api.get<ListEnvelope<ItDirection>>('/it-directions', {
    query: { limit: 200, ...(activeOnly ? { is_active: 'true' } : {}) },
    signal,
  });
}

export interface ItDirectionPayload {
  code: string;
  name: string;
  description?: string | null;
  sort_order?: number;
  is_active?: boolean;
}

export function createItDirection(payload: ItDirectionPayload): Promise<ItDirection> {
  return api.post<ItDirection>('/it-directions', { body: payload });
}

export function updateItDirection(
  id: string,
  patch: Partial<ItDirectionPayload> & { version: number },
): Promise<ItDirection> {
  return api.patch<ItDirection>(`/it-directions/${id}`, { body: patch });
}

/** Мягкое удаление: направление деактивируется, связи с программами и продуктами остаются. */
export function deactivateItDirection(id: string): Promise<void> {
  return api.delete<void>(`/it-directions/${id}`);
}
