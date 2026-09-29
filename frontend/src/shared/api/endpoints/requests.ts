import { api } from '../client';
import type {
  AvailableTransition,
  FileObject,
  ListEnvelope,
  RequestComment,
  RequestHistoryItem,
  RequestItem,
  WorkflowType,
} from '../types';

/** Заявки B2B/B2C (api-contract.md §4). В БД — deal, в UI — «заявка». */

export interface RequestListFilters {
  workflow_type?: WorkflowType;
  status_id?: string[];
  assignee_id?: string;
  university_id?: string;
  product_id?: string;
  program_id?: string;
  stuck?: boolean;
  created_from?: string;
  created_to?: string;
  source?: string;
  search?: string;
}

export function listRequests(
  filters: RequestListFilters,
  options: { limit?: number; offset?: number; sort?: string; signal?: AbortSignal } = {},
): Promise<ListEnvelope<RequestItem>> {
  return api.get<ListEnvelope<RequestItem>>('/requests', {
    query: {
      limit: options.limit ?? 25,
      offset: options.offset ?? 0,
      sort: options.sort,
      ...filters,
    },
    signal: options.signal,
  });
}

export function getRequest(id: string, signal?: AbortSignal): Promise<RequestItem> {
  return api.get<RequestItem>(`/requests/${id}`, { signal });
}

/**
 * Тело создания заявки (§4.1: статус всегда initial схемы, менять нельзя).
 * B2B: university_id обязателен. B2C: контрагент физ/юрлицо создаётся вместе
 * с заявкой — блок `client` (как в сериализации §4.2) + `client_kind` +
 * `interaction_type_id` из редактируемого справочника (data-model.md §4.4).
 */
export interface CreateRequestPayload {
  workflow_type: WorkflowType;
  title: string;
  university_id?: string;
  client_kind?: 'person' | 'company';
  interaction_type_id?: string;
  client?: {
    full_name?: string;
    email?: string;
    phone?: string;
    company_name?: string;
    inn?: string;
  };
  contract_id?: string | null;
  product_id?: string | null;
  program_id?: string | null;
  assignee_id?: string;
  amount?: string | null;
  description?: string | null;
}

export function createRequest(payload: CreateRequestPayload): Promise<RequestItem> {
  return api.post<RequestItem>('/requests', {
    body: payload,
    idempotencyKey: crypto.randomUUID(),
  });
}

export interface UpdateRequestPayload {
  title?: string;
  contract_id?: string | null;
  product_id?: string | null;
  program_id?: string | null;
  amount?: string | null;
  description?: string | null;
  interaction_type_id?: string | null;
  version: number;
}

export function updateRequest(id: string, patch: UpdateRequestPayload): Promise<RequestItem> {
  return api.patch<RequestItem>(`/requests/${id}`, { body: patch });
}

/** Смена ответственного (§4.1): admin/head_kam; kam — взять свободную на себя. */
export function assignRequest(id: string, assigneeId: string): Promise<RequestItem> {
  return api.post<RequestItem>(`/requests/${id}/assign`, { body: { assignee_id: assigneeId } });
}

/** Доступные переходы текущего пользователя, вкл. возвраты (workflow-engine.md §7). */
export function listRequestTransitions(
  id: string,
  signal?: AbortSignal,
): Promise<{ items: AvailableTransition[] }> {
  return api.get<{ items: AvailableTransition[] }>(`/requests/${id}/transitions`, { signal });
}

export interface TransitionPayload {
  to_status_id: string;
  comment?: string;
  version: number;
}

/** Переход по статусу (§4.3): 409 workflow_invalid_transition / stale_version. */
export function performTransition(id: string, payload: TransitionPayload): Promise<RequestItem> {
  return api.post<RequestItem>(`/requests/${id}/transitions`, {
    body: payload,
    idempotencyKey: crypto.randomUUID(),
  });
}

export function listRequestHistory(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<RequestHistoryItem>> {
  return api.get<ListEnvelope<RequestHistoryItem>>(`/requests/${id}/history`, {
    query: { limit: 200 },
    signal,
  });
}

// --- Комментарии (§4.4) ------------------------------------------------------

export function listRequestComments(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<RequestComment>> {
  return api.get<ListEnvelope<RequestComment>>(`/requests/${id}/comments`, {
    query: { limit: 200 },
    signal,
  });
}

export function createRequestComment(id: string, text: string): Promise<RequestComment> {
  return api.post<RequestComment>(`/requests/${id}/comments`, { body: { text } });
}

export function deleteRequestComment(id: string, commentId: string): Promise<void> {
  return api.delete<void>(`/requests/${id}/comments/${commentId}`);
}

// --- Файлы заявки ------------------------------------------------------------
// Симметрично файлам договора/студента (§3.2, §8.1): file_object с
// entity_type='deal' (data-model.md §9.2); скачивание — общий /files/{id}/download.

export function listRequestFiles(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<FileObject>> {
  return api.get<ListEnvelope<FileObject>>(`/requests/${id}/files`, { signal });
}

export function uploadRequestFile(id: string, file: File): Promise<FileObject> {
  const form = new FormData();
  form.append('file', file);
  return api.post<FileObject>(`/requests/${id}/files`, { body: form });
}

export function deleteRequestFile(id: string, fileId: string): Promise<void> {
  return api.delete<void>(`/requests/${id}/files/${fileId}`);
}
