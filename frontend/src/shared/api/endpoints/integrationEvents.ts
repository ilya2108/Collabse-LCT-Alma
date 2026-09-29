import { api } from '../client';
import type { IntegrationEventRecord, ListEnvelope } from '../types';

/**
 * Журнал интеграционных событий LMS/CMS — `GET /admin/integration/events`
 * (api-contract.md §10.4). Роли: admin, observer. Карточка заявки использует
 * его для блока «Интеграции» только когда роль позволяет; остальным
 * показываются `external_refs` из тела заявки.
 */

export interface IntegrationEventFilters {
  direction?: 'outbound' | 'inbound';
  system?: 'lms' | 'cms';
  status?: string;
  entity_id?: string;
  entity_type?: string;
}

export function listIntegrationEvents(
  filters: IntegrationEventFilters = {},
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<ListEnvelope<IntegrationEventRecord>> {
  return api.get<ListEnvelope<IntegrationEventRecord>>('/admin/integration/events', {
    query: { limit: options.limit ?? 50, ...filters },
    signal: options.signal,
  });
}

/** Повторить доставку события из DLQ (только admin). */
export function retryIntegrationEvent(eventId: string): Promise<IntegrationEventRecord> {
  return api.post<IntegrationEventRecord>(`/admin/integration/events/${eventId}/retry`);
}
