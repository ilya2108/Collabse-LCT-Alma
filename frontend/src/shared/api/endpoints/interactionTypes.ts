import { api } from '../client';
import type { InteractionType, ListEnvelope } from '../types';

/**
 * Типы взаимодействия B2C — редактируемый справочник `interaction_type`
 * (data-model.md §4.4, модуль crm в OVERVIEW.md §4.1). Обязателен для формы
 * создания B2C-заявки (api-contract.md §4.2: `interaction_type_id`).
 */
export function listInteractionTypes(signal?: AbortSignal): Promise<ListEnvelope<InteractionType>> {
  return api.get<ListEnvelope<InteractionType>>('/interaction-types', {
    query: { limit: 100, is_active: 'true' },
    signal,
  });
}
