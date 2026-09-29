import { getFreshToken } from '@/shared/auth/keycloak';
import { env } from '@/shared/config/env';
import { api } from '../client';
import { apiErrorFromResponse, networkError } from '../errors';
import type { ExportFormat, ExportJob } from '../types';

/**
 * Экспорт данных — `/export/jobs` (api-contract.md §7.1): XLSX/CSV/JSON,
 * ≤10 000 строк — синхронный `done`, больше — поллинг статуса. RBAC серверный:
 * выгружается ровно то, что пользователь видит в списках; ПДн студентов для
 * observer — маскированные.
 */

export interface CreateExportJobPayload {
  entity_type: string;
  format: ExportFormat;
  filters?: Record<string, unknown>;
  columns?: string[];
  options?: { encoding?: 'utf-8' | 'cp1251'; csv_delimiter?: string };
}

export function createExportJob(payload: CreateExportJobPayload): Promise<ExportJob> {
  return api.post<ExportJob>('/export/jobs', { body: payload });
}

export function getExportJob(id: string, signal?: AbortSignal): Promise<ExportJob> {
  return api.get<ExportJob>(`/export/jobs/${id}`, { signal });
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 120_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Полный цикл «создать задание → дождаться → скачать». Возвращает имя
 * скачанного файла; бросает ApiError/Error с русским сообщением.
 */
export async function runExportAndDownload(
  payload: CreateExportJobPayload,
  fallbackName: string,
): Promise<string> {
  let job = await createExportJob(payload);
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (job.status === 'pending' || job.status === 'running') {
    if (Date.now() > deadline) {
      throw new Error('Выгрузка готовится слишком долго — попробуйте позже');
    }
    await sleep(POLL_INTERVAL_MS);
    job = await getExportJob(job.id);
  }
  if (job.status === 'failed') {
    throw new Error(job.error || 'Выгрузка завершилась с ошибкой');
  }
  const name = job.file?.file_name ?? `${fallbackName}.${payload.format}`;
  await downloadExportJob(job.id, name);
  return name;
}

/**
 * Скачивание результата: `GET /export/jobs/{id}/download` отдаёт поток или
 * 302 на presigned URL (fetch следует за редиректом сам); Bearer-заголовок
 * обычной навигацией не передать — качаем в blob.
 */
export async function downloadExportJob(id: string, fileName: string): Promise<void> {
  const token = await getFreshToken();
  let response: Response;
  try {
    response = await fetch(`${env.apiUrl}/export/jobs/${id}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
  } catch {
    throw networkError();
  }
  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    throw apiErrorFromResponse(response.status, body);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = fileName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
