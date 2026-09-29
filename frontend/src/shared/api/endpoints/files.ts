import { getFreshToken } from '@/shared/auth/keycloak';
import { env } from '@/shared/config/env';
import { apiErrorFromResponse, networkError } from '../errors';

/**
 * Скачивание файла — `GET /files/{file_id}/download` (api-contract.md §3.2):
 * backend проверяет RBAC и отдаёт поток (режим proxy по умолчанию) либо 302
 * на presigned MinIO URL. Обычная навигация браузера не умеет Bearer-заголовок,
 * поэтому качаем fetch'ем в blob и отдаём пользователю через <a download>.
 */
export async function downloadFile(fileId: string, fileName: string): Promise<void> {
  const token = await getFreshToken();
  let response: Response;
  try {
    response = await fetch(`${env.apiUrl}/files/${fileId}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      // fetch сам следует за 302 на presigned URL (redirect: 'follow' — дефолт)
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
    // даём браузеру время начать скачивание до revoke
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

/** «482 КБ» / «1,2 МБ» для списков файлов. */
export function formatFileSize(sizeBytes: number | null | undefined): string {
  if (!sizeBytes || sizeBytes <= 0) return '—';
  if (sizeBytes < 1024) return `${sizeBytes} Б`;
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} КБ`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
}
