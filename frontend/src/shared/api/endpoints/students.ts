import type { RegistryFetchParams } from '@/components/RegistryTable/types';
import { api } from '../client';
import type {
  FileObject,
  ListEnvelope,
  Student,
  StudentFunnelStatus,
  StudentHistoryItem,
  StudentRevealResult,
} from '../types';

/**
 * Talent Pool — `/students` (api-contract.md §8). Все выдачи приходят с
 * маскированными ПДн; открытые значения возвращает только reveal (с аудитом
 * на backend). Никакого локального кэширования раскрытых ПДн.
 */

export function listStudents(params: RegistryFetchParams): Promise<ListEnvelope<Student>> {
  const { filters, signal, ...rest } = params;
  return api.get<ListEnvelope<Student>>('/students', {
    query: { ...rest, ...filters },
    signal,
  });
}

export function getStudent(id: string, signal?: AbortSignal): Promise<Student> {
  return api.get<Student>(`/students/${id}`, { signal });
}

export interface StudentPayload {
  full_name: string;
  email: string;
  phone?: string | null;
  university_id?: string | null;
  program_id?: string | null;
  federal_project_id?: string | null;
  funnel_status?: StudentFunnelStatus;
  notes?: string | null;
}

export function createStudent(payload: StudentPayload): Promise<Student> {
  return api.post<Student>('/students', { body: payload });
}

export function updateStudent(
  id: string,
  patch: Partial<StudentPayload> & { version: number },
): Promise<Student> {
  return api.patch<Student>(`/students/${id}`, { body: patch });
}

/** Раскрытие ПДн: backend пишет аудит-событие `student.pii_revealed` (§8.1). */
export function revealStudent(id: string): Promise<StudentRevealResult> {
  return api.post<StudentRevealResult>(`/students/${id}/reveal`);
}

export interface StudentStatusPayload {
  to: StudentFunnelStatus;
  comment?: string;
  version: number;
}

/** Переход по воронке (§8.2): вперёд — соседний, назад — любой с комментарием. */
export function changeStudentStatus(id: string, payload: StudentStatusPayload): Promise<Student> {
  return api.post<Student>(`/students/${id}/status`, { body: payload });
}

export function listStudentHistory(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<StudentHistoryItem>> {
  return api.get<ListEnvelope<StudentHistoryItem>>(`/students/${id}/history`, { signal });
}

export function listStudentFiles(
  id: string,
  signal?: AbortSignal,
): Promise<ListEnvelope<FileObject>> {
  return api.get<ListEnvelope<FileObject>>(`/students/${id}/files`, { signal });
}

export function uploadStudentFile(id: string, file: File): Promise<FileObject> {
  const form = new FormData();
  form.append('file', file);
  return api.post<FileObject>(`/students/${id}/files`, { body: form });
}

export function deleteStudentFile(id: string, fileId: string): Promise<void> {
  return api.delete<void>(`/students/${id}/files/${fileId}`);
}
