import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type { ListEnvelope, University, UniversityContact } from '../types';

/** Реестр вузов и контактные лица (api-contract.md §3.1). */

export function listUniversities(params: RegistryFetchParams): Promise<ListEnvelope<University>> {
  return api.get<ListEnvelope<University>>('/universities', {
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

/** Лёгкий список для селектов: серверный поиск, первые N записей. */
export function searchUniversities(
  search: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<University>> {
  return api.get<ListEnvelope<University>>('/universities', {
    query: { limit: 20, search: search || undefined },
    signal,
  });
}

export function getUniversity(id: string, signal?: AbortSignal): Promise<University> {
  return api.get<University>(`/universities/${id}`, { signal });
}

export interface UniversityPayload {
  name: string;
  short_name?: string | null;
  inn?: string | null;
  kpp?: string | null;
  region?: string | null;
  city?: string | null;
  website?: string | null;
  kam_user_id?: string | null;
  notes?: string | null;
}

export function createUniversity(payload: UniversityPayload): Promise<University> {
  return api.post<University>('/universities', { body: payload });
}

export function updateUniversity(
  id: string,
  patch: Partial<UniversityPayload> & { version: number },
): Promise<University> {
  return api.patch<University>(`/universities/${id}`, { body: patch });
}

// --- Контактные лица ---------------------------------------------------------

export function listUniversityContacts(
  universityId: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<UniversityContact>> {
  return api.get<ListEnvelope<UniversityContact>>(`/universities/${universityId}/contacts`, {
    signal,
  });
}

export interface UniversityContactPayload {
  full_name: string;
  position?: string | null;
  email?: string | null;
  phone?: string | null;
}

export function createUniversityContact(
  universityId: string,
  payload: UniversityContactPayload,
): Promise<UniversityContact> {
  return api.post<UniversityContact>(`/universities/${universityId}/contacts`, { body: payload });
}

export function updateUniversityContact(
  universityId: string,
  contactId: string,
  patch: Partial<UniversityContactPayload>,
): Promise<UniversityContact> {
  return api.patch<UniversityContact>(`/universities/${universityId}/contacts/${contactId}`, {
    body: patch,
  });
}

export function deleteUniversityContact(universityId: string, contactId: string): Promise<void> {
  return api.delete<void>(`/universities/${universityId}/contacts/${contactId}`);
}
