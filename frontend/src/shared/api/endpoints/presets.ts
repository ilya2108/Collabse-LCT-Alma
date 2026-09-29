import { api } from '../client';
import type { ListEnvelope, UiPreset, UiPresetState } from '../types';

/** Сохранённые пресеты таблиц/фильтров, per-user (api-contract.md §2.2). */

export function listPresets(screen: string, signal?: AbortSignal): Promise<ListEnvelope<UiPreset>> {
  return api.get<ListEnvelope<UiPreset>>('/ui/presets', { query: { screen }, signal });
}

export interface CreatePresetPayload {
  screen: string;
  name: string;
  is_default: boolean;
  state: UiPresetState;
}

export function createPreset(payload: CreatePresetPayload): Promise<UiPreset> {
  return api.post<UiPreset>('/ui/presets', { body: payload });
}

export function updatePreset(
  id: string,
  patch: Partial<Omit<CreatePresetPayload, 'screen'>>,
): Promise<UiPreset> {
  return api.patch<UiPreset>(`/ui/presets/${id}`, { body: patch });
}

export function deletePreset(id: string): Promise<void> {
  return api.delete<void>(`/ui/presets/${id}`);
}
