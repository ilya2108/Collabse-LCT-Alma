import { getFreshToken, keycloak } from '@/shared/auth/keycloak';
import { env } from '@/shared/config/env';
import { apiErrorFromResponse, networkError } from './errors';

/**
 * Fetch-обёртка над REST API backend'а (api-contract.md §1):
 * - Bearer-токен Keycloak с тихим refresh перед запросом;
 * - повтор запроса один раз после форс-refresh на 401;
 * - разбор единого формата ошибок `{error:{...}}` → ApiError;
 * - `X-Request-Id` на каждый запрос (trace_id в ответе ошибки);
 * - сериализация query-параметров (массив — повтором параметра, §1.5).
 */

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  query?: Record<string, QueryValue | QueryValue[]>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Idempotency-Key для POST-мутаций (§1.6). */
  idempotencyKey?: string;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${env.apiUrl}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item === null || item === undefined || item === '') continue;
      params.append(key, String(item));
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function doFetch(
  method: string,
  path: string,
  options: RequestOptions,
  token: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-Request-Id': crypto.randomUUID(),
    ...options.headers,
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;

  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    body = options.body;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json; charset=utf-8';
    body = JSON.stringify(options.body);
  }

  try {
    return await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw networkError();
  }
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  let token = await getFreshToken();
  let response = await doFetch(method, path, options, token);

  // 401: пробуем форс-refresh и повторяем один раз (ux.md §3.4).
  // Если refresh не удался, keycloak-js вызовет onAuthRefreshError →
  // AuthProvider покажет модалку «Сессия истекла».
  if (response.status === 401 && keycloak.authenticated) {
    try {
      await keycloak.updateToken(-1);
      token = keycloak.token ?? null;
    } catch {
      token = null;
    }
    if (token) {
      response = await doFetch(method, path, options, token);
    }
  }

  const body = await parseBody(response);
  if (!response.ok) {
    throw apiErrorFromResponse(response.status, body);
  }
  return body as T;
}

export const api = {
  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>('GET', path, options);
  },
  post<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>('POST', path, options);
  },
  put<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>('PUT', path, options);
  },
  patch<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>('PATCH', path, options);
  },
  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return request<T>('DELETE', path, options);
  },
};
