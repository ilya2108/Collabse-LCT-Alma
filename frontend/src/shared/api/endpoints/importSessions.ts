import { api } from '../client';
import type {
  ImportEntityType,
  ImportMappingEntry,
  ImportMappingOptions,
  ImportSession,
  ImportValidationReport,
} from '../types';

/**
 * Сессии импорта (api-contract.md §6.1): upload → распознавание → маппинг →
 * валидация → применение. Состояние живёт на сервере (F5 не убивает прогресс).
 */

export function createImportSession(
  file: File,
  entityType: ImportEntityType,
): Promise<ImportSession> {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('entity_type', entityType);
  return api.post<ImportSession>('/import/sessions', { body: form });
}

export function getImportSession(id: string, signal?: AbortSignal): Promise<ImportSession> {
  return api.get<ImportSession>(`/import/sessions/${id}`, { signal });
}

export interface PutMappingPayload {
  mapping: ImportMappingEntry[];
  options: ImportMappingOptions;
}

/**
 * Сохранить маппинг и опции (§6.3). Смена листа/кодировки/заголовка тоже идёт
 * этим PUT'ом (возможно, с пустым `mapping`) — сервер перечитывает файл и
 * возвращает сессию с обновлённым `detected` (превью перерисовывается).
 */
export function putImportMapping(id: string, payload: PutMappingPayload): Promise<ImportSession> {
  return api.put<ImportSession>(`/import/sessions/${id}/mapping`, { body: payload });
}

export function validateImportSession(id: string): Promise<ImportValidationReport> {
  return api.post<ImportValidationReport>(`/import/sessions/${id}/validate`);
}

export interface ApplyImportPayload {
  /** skip_errors — грузим валидные; all_or_nothing — 422, если есть ошибки (§6.4). */
  mode: 'skip_errors' | 'all_or_nothing';
}

/**
 * Применение (§6.4): до 5 000 строк — синхронно (сессия в ответе `completed`),
 * больше — `202 {"state":"applying"}` и поллинг GET раз в 2 секунды.
 */
export function applyImportSession(id: string, payload: ApplyImportPayload): Promise<ImportSession> {
  return api.post<ImportSession>(`/import/sessions/${id}/apply`, {
    body: payload,
    idempotencyKey: crypto.randomUUID(),
  });
}

export function deleteImportSession(id: string): Promise<void> {
  return api.delete<void>(`/import/sessions/${id}`);
}
