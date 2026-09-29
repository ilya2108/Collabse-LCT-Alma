import type { ApiErrorBody, ApiErrorDetail } from './types';

/**
 * Ошибка API в едином формате api-contract.md §1.4.
 * `message` — человекочитаемый русский текст, показывается в UI как есть.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetail[];
  readonly traceId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: ApiErrorDetail[] = [],
    traceId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.traceId = traceId;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

const STATUS_FALLBACK_MESSAGES: Record<number, string> = {
  400: 'Некорректный запрос',
  401: 'Требуется вход в систему',
  403: 'Недостаточно прав для этого действия',
  404: 'Объект не найден',
  409: 'Конфликт данных: обновите страницу и повторите',
  413: 'Файл слишком большой',
  422: 'Данные не прошли проверку',
  429: 'Слишком много запросов, повторите позже',
  500: 'Внутренняя ошибка сервера',
  502: 'Внешний сервис недоступен',
  503: 'Сервис временно недоступен',
};

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === 'object' &&
    value !== null &&
    'error' in value &&
    typeof (value as { error: unknown }).error === 'object' &&
    (value as { error: unknown }).error !== null &&
    typeof (value as ApiErrorBody).error.message === 'string'
  );
}

/** Собирает ApiError из HTTP-ответа: тело `{error:{...}}` или фолбэк по статусу. */
export function apiErrorFromResponse(status: number, body: unknown): ApiError {
  if (isApiErrorBody(body)) {
    const { code, message, details, trace_id } = body.error;
    return new ApiError(status, code, message, details ?? [], trace_id ?? null);
  }
  const message =
    STATUS_FALLBACK_MESSAGES[status] ?? `Не удалось выполнить запрос (HTTP ${status})`;
  return new ApiError(status, 'unknown_error', message);
}

/** Сетевая ошибка (fetch упал, сервер недоступен). */
export function networkError(): ApiError {
  return new ApiError(
    0,
    'network_error',
    'Нет связи с сервером. Проверьте подключение и повторите.',
  );
}

/** Человекочитаемое сообщение из любой ошибки — для тостов и состояний экрана. */
export function errorMessage(error: unknown, fallback = 'Что-то пошло не так'): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
