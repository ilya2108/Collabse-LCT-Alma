import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type {
  AdminSettings,
  AdminUser,
  AuditLogRecord,
  DictionaryImportResult,
  DictionaryInfo,
  DictionaryItem,
  FeatureFlag,
  FlagsMap,
  ListEnvelope,
} from '../types';

/**
 * Admin-зона (api-contract.md §10) + фичефлаги (§2.2). Все ручки, кроме
 * `GET /flags` и чтения аудита observer'ом, доступны только роли admin —
 * серверная RBAC дублирует guard'ы фронта.
 */

// --- Пользователи (§10.2) ---------------------------------------------------

/** Иерархия эскалаций: у пользователя меняется только `manager_id` (§10.2). */
export function updateAdminUser(id: string, managerId: string | null): Promise<AdminUser> {
  return api.patch<AdminUser>(`/admin/users/${id}`, { body: { manager_id: managerId } });
}

/** Принудительная синхронизация PG-реплики из Keycloak Admin API (§10.2). */
export function syncAdminUsers(): Promise<{ synced?: number }> {
  return api.post<{ synced?: number }>('/admin/users/sync');
}

// --- Настройки системы (§10.3) ----------------------------------------------

export function getAdminSettings(signal?: AbortSignal): Promise<AdminSettings> {
  return api.get<AdminSettings>('/admin/settings', { signal });
}

export function putAdminSettings(settings: AdminSettings): Promise<AdminSettings> {
  return api.put<AdminSettings>('/admin/settings', { body: settings });
}

// --- Справочники (§10.1) -----------------------------------------------------

export function listDictionaries(signal?: AbortSignal): Promise<DictionaryInfo[]> {
  return api
    .get<DictionaryInfo[] | { items: DictionaryInfo[] }>('/admin/dictionaries', { signal })
    .then((data) => (Array.isArray(data) ? data : data.items));
}

export function listDictionaryItems(
  name: string,
  params: Pick<RegistryFetchParams, 'limit' | 'offset' | 'search' | 'signal'>,
): Promise<ListEnvelope<DictionaryItem>> {
  const { signal, ...query } = params;
  return api.get<ListEnvelope<DictionaryItem>>(`/admin/dictionaries/${name}/items`, {
    query,
    signal,
  });
}

/** JSON-предзагрузка справочника (§10.1, путь 1 — «JSON напрямую»). */
export function importDictionaryJson(
  name: string,
  items: unknown[],
  updateStrategy: 'upsert' | 'create_only' = 'upsert',
): Promise<DictionaryImportResult> {
  return api.post<DictionaryImportResult>(`/admin/dictionaries/${name}/import`, {
    body: { items, update_strategy: updateStrategy },
  });
}

// --- Фичефлаги (§2.2) --------------------------------------------------------

/** Активные флаги для всех ролей — рендер UI-элементов под флагом. */
export function getFlags(signal?: AbortSignal): Promise<FlagsMap> {
  return api.get<FlagsMap>('/flags', { signal });
}

export function listFeatureFlags(signal?: AbortSignal): Promise<FeatureFlag[]> {
  return api
    .get<FeatureFlag[] | { items: FeatureFlag[] }>('/admin/feature-flags', { signal })
    .then((data) => (Array.isArray(data) ? data : data.items));
}

/** Переключение флага: backend публикует SSE `flags.updated` всем клиентам. */
export function patchFeatureFlag(name: string, enabled: boolean): Promise<FeatureFlag> {
  return api.patch<FeatureFlag>(`/admin/feature-flags/${name}`, { body: { enabled } });
}

// --- Аудит (§10.4) -----------------------------------------------------------

export interface AuditLogFilters {
  action?: string;
  entity_type?: string;
  entity_id?: string;
  actor_id?: string;
  from?: string;
  to?: string;
}

export function listAuditLog(
  filters: AuditLogFilters,
  options: { limit: number; offset: number; signal?: AbortSignal },
): Promise<ListEnvelope<AuditLogRecord>> {
  const { signal, ...paging } = options;
  return api.get<ListEnvelope<AuditLogRecord>>('/admin/audit-log', {
    query: { ...paging, ...filters },
    signal,
  });
}
