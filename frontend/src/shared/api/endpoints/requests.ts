import { api } from '../client';
import { isApiError } from '../errors';
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
  /** КАМ, вуз и продукт — один id или несколько (повтор параметра, условие IN на сервере). */
  assignee_id?: string | string[];
  university_id?: string | string[];
  product_id?: string | string[];
  program_id?: string;
  stuck?: boolean;
  /** Без ответственного КАМа: ответственный не КАМ процесса или деактивирован. */
  unassigned?: boolean;
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
 * B2B: university_id обязателен. B2C: существующий клиент — `counterparty_id`,
 * новый — блок `client` (как в сериализации §4.2) + `client_kind`: сервер находит
 * клиента в базе (физлицо — по email, юрлицо — по ИНН или названию) или создаёт его;
 * `interaction_type_id` — из редактируемого справочника (data-model.md §4.4).
 * Передаётся что-то одно: `counterparty_id` или `client`.
 */
export interface CreateRequestPayload {
  workflow_type: WorkflowType;
  title: string;
  university_id?: string;
  counterparty_id?: string;
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

/** Ответ создания: заявка + подсказки сервера, которых нет в карточке. */
export interface CreatedRequest extends RequestItem {
  /** B2B: у вуза уже несколько активных заявок — не дубль ли это. */
  warning_duplicate?: string;
  /** B2C с `client`: true — клиент с таким email/ИНН уже был в базе, заявка привязана к нему. */
  client_matched?: boolean;
}

export function createRequest(payload: CreateRequestPayload): Promise<CreatedRequest> {
  return api.post<CreatedRequest>('/requests', { body: payload });
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

/** Лимит комментария к переходу — как на сервере (TransitionBody.comment). */
export const TRANSITION_COMMENT_MAX = 4000;
/** Файлов на одном переходе — не больше (TransitionBody.file_ids). */
export const TRANSITION_FILES_MAX = 10;

export interface TransitionPayload {
  to_status_id: string;
  comment?: string;
  version: number;
  /** Файлы, загруженные «для перехода» (`pending_transition`): сервер приложит их к переходу. */
  file_ids?: string[];
}

/** Переход по статусу (§4.3): 409 workflow_invalid_transition / stale_version. */
export function performTransition(id: string, payload: TransitionPayload): Promise<RequestItem> {
  return api.post<RequestItem>(`/requests/${id}/transitions`, { body: payload });
}

/**
 * Переход с файлами (ФТ3 «файлы в статусах»): файлы загружаются в заявку «для перехода»,
 * затем переход приносит их `file_ids` — сервер привязывает файлы к строке истории
 * перехода. Если переход отклонён (4xx: конфликт версии, обязательный комментарий,
 * права), загруженные файлы удаляются — повтор из модалки загрузит их заново. При
 * сетевой ошибке и 5xx файлы не трогаем: переход мог состояться.
 */
export async function transitionWithFiles(
  id: string,
  payload: Omit<TransitionPayload, 'file_ids'>,
  files: File[] = [],
): Promise<RequestItem> {
  const uploaded: RequestFile[] = [];
  try {
    for (const file of files) {
      uploaded.push(await uploadRequestFile(id, file, { pendingTransition: true }));
    }
    return await performTransition(id, {
      ...payload,
      file_ids: uploaded.length > 0 ? uploaded.map((f) => f.id) : undefined,
    });
  } catch (error) {
    if (uploaded.length > 0 && isApiError(error) && error.status >= 400 && error.status < 500) {
      await Promise.allSettled(uploaded.map((f) => deleteRequestFile(id, f.id)));
    }
    throw error;
  }
}

/** Элемент истории с файлами, приложенными к этому переходу (входу в этап). */
export interface RequestHistoryEntry extends RequestHistoryItem {
  files?: RequestFile[];
}

export function listRequestHistory(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<RequestHistoryEntry>> {
  return api.get<ListEnvelope<RequestHistoryEntry>>(`/requests/${id}/history`, {
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
// Файлы «в статусах» (ФТ3): у файла есть этап — переход, на котором его приложили, или
// вход в этап, где его загрузили из вкладки «Файлы».

/** Белый список вложений заявки — как на сервере (`ATTACHMENT_FORMATS`), 10 форматов ТЗ. */
export const REQUEST_FILE_ACCEPT = [
  '.png',
  '.jpg',
  '.jpeg',
  '.pdf',
  '.zip',
  '.gz',
  '.gzip',
  '.tgz',
  '.rar',
  '.doc',
  '.docx',
  '.xls',
  '.xlsx',
];
export const REQUEST_FILE_FORMATS_LABEL = 'PNG, JPEG, PDF, ZIP, GZIP, RAR, DOC, DOCX, XLS, XLSX';
export const REQUEST_FILE_MAX_MB = 50;

/** Понятная причина отказа до загрузки (формат, размер) или null — файл подходит. */
export function requestFileProblem(file: File): string | null {
  const dot = file.name.lastIndexOf('.');
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
  if (!REQUEST_FILE_ACCEPT.includes(ext)) {
    return `«${file.name}»: формат ${ext || 'без расширения'} не поддерживается. Можно: ${REQUEST_FILE_FORMATS_LABEL}`;
  }
  if (file.size > REQUEST_FILE_MAX_MB * 1024 * 1024) {
    return `«${file.name}»: больше ${REQUEST_FILE_MAX_MB} МБ`;
  }
  if (file.size === 0) return `«${file.name}»: файл пуст`;
  return null;
}

/** Файл заявки: этап (снапшот имени из истории), к которому он приложен, и автор. */
export interface RequestFile extends FileObject {
  stage_history_id: string | null;
  stage_name: string | null;
}

export function listRequestFiles(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<RequestFile>> {
  return api.get<ListEnvelope<RequestFile>>(`/requests/${id}/files`, { signal });
}

export interface UploadRequestFileOptions {
  /** Файл для перехода из модалки: привяжется к переходу через `file_ids`. */
  pendingTransition?: boolean;
  /** Приложить к прошлому переходу (строке истории) этой заявки. */
  stageHistoryId?: string;
}

export function uploadRequestFile(
  id: string,
  file: File,
  options: UploadRequestFileOptions = {},
): Promise<RequestFile> {
  const form = new FormData();
  form.append('file', file);
  if (options.pendingTransition) form.append('pending_transition', 'true');
  if (options.stageHistoryId) form.append('stage_history_id', options.stageHistoryId);
  return api.post<RequestFile>(`/requests/${id}/files`, { body: form });
}

export function deleteRequestFile(id: string, fileId: string): Promise<void> {
  return api.delete<void>(`/requests/${id}/files/${fileId}`);
}
