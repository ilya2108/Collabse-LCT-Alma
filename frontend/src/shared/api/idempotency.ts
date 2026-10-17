/**
 * Ключи идемпотентности мутаций на клиенте (api-contract.md §1.6).
 *
 * - «Одно действие пользователя = один ключ»: ключ выводится из отпечатка запроса
 *   (метод + URL + стабильное тело) и переиспользуется, пока нет окончательного ответа.
 *   Ретрай после таймаута, «Повторить» после обрыва связи и двойной клик получают
 *   сохранённый ответ сервера (`Idempotent-Replayed: true`), а не дубль операции.
 * - Автоповтор с тем же ключом: сетевая ошибка и 502/503/504 — до 3 попыток
 *   (паузы ~0,5 и ~1,5 с или по `Retry-After`); 409 `idempotency_in_progress` (первый
 *   запрос ещё выполняется) и 409 `workflow_busy` (схема процесса заблокирована
 *   переводом заявок — операция не выполнялась) — ожидание по `Retry-After`,
 *   суммарно не дольше 10 с.
 * - 422 `idempotency_key_reused` (ложное совпадение отпечатка, напр. тот же файл
 *   с другим содержимым) — один повтор с новым ключом.
 *
 * Модуль без импортов: юнит-тесты гоняются в node (`npm run test:unit`).
 */

export const MUTATING_METHODS: ReadonlySet<string> = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

const DEFAULT_TTL_MS = 10 * 60_000;
const DEFAULT_MAX_ENTRIES = 500;
const BACKOFF_MS = [500, 1500];
const MIN_WAIT_MS = 500;
const MAX_WAIT_MS = 5000;

/** UUID v4: `crypto.randomUUID`, а где его нет — из `getRandomValues`. */
export function newIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof cryptoApi?.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  const parts = [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20)];
  return `${parts.join('-')}-${hex.slice(20)}`;
}

function normalize(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  const withJson = value as { toJSON?: () => unknown };
  if (typeof withJson.toJSON === 'function') return normalize(withJson.toJSON());
  if (Array.isArray(value)) return value.map((item) => normalize(item));
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) {
    const item = (value as Record<string, unknown>)[key];
    if (item === undefined || typeof item === 'function' || typeof item === 'symbol') continue;
    result[key] = normalize(item);
  }
  return result;
}

/** JSON с рекурсивно отсортированными ключами; `undefined` в объектах опускается. */
export function stableStringify(value: unknown): string {
  return JSON.stringify(normalize(value)) ?? '';
}

interface FileFingerprint {
  file: string;
  size: number;
  type: string;
  lastModified: number;
}

function describeFile(blob: Blob): FileFingerprint {
  const file = blob as Partial<File>;
  return {
    file: file.name ?? '',
    size: blob.size,
    type: blob.type,
    lastModified: file.lastModified ?? 0,
  };
}

function describeBody(body: unknown): string {
  if (body === undefined) return '';
  if (typeof FormData !== 'undefined' && body instanceof FormData) {
    const entries: [string, string | FileFingerprint][] = [];
    body.forEach((value, name) => {
      entries.push([name, typeof value === 'string' ? value : describeFile(value)]);
    });
    return `form:${stableStringify(entries)}`;
  }
  return `json:${stableStringify(body)}`;
}

/**
 * Отпечаток действия: метод + URL (с query) + стабильное тело.
 * FormData → `[[name, string | {file, size, type, lastModified}], ...]` — содержимое
 * файла не читается; сервер сверит его дайджест сам (422 → новый ключ).
 */
export function requestFingerprint(method: string, url: string, body: unknown): string {
  return `${method.toUpperCase()} ${url}\n${describeBody(body)}`;
}

export interface IdempotencyKeyRegistryOptions {
  /** Сколько живёт ключ без окончательного ответа (по умолчанию 10 мин; сервер хранит 24 ч). */
  ttlMs?: number;
  /** Не больше стольких действий в реестре (по умолчанию 500, старые вытесняются). */
  maxEntries?: number;
  now?: () => number;
  newKey?: () => string;
}

interface RegistryEntry {
  key: string;
  expiresAt: number;
}

/** Реестр «отпечаток действия → ключ»: ключ живёт, пока на действие нет окончательного ответа. */
export class IdempotencyKeyRegistry {
  private readonly entries = new Map<string, RegistryEntry>();
  private readonly ttlMs: number;
  private readonly maxEntries: number;
  private readonly now: () => number;
  private readonly newKey: () => string;

  constructor(opts: IdempotencyKeyRegistryOptions = {}) {
    this.ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS;
    this.maxEntries = Math.max(1, opts.maxEntries ?? DEFAULT_MAX_ENTRIES);
    this.now = opts.now ?? Date.now;
    this.newKey = opts.newKey ?? newIdempotencyKey;
  }

  /** Существующий непросроченный ключ действия или новый. */
  acquire(fingerprint: string): string {
    const now = this.now();
    const existing = this.entries.get(fingerprint);
    if (existing && existing.expiresAt > now) return existing.key;
    this.entries.delete(fingerprint);
    this.prune(now);
    const key = this.newKey();
    this.entries.set(fingerprint, { key, expiresAt: now + this.ttlMs });
    return key;
  }

  /** Окончательный ответ получен — следующее такое же действие получит новый ключ. */
  settle(fingerprint: string, key: string): void {
    if (this.entries.get(fingerprint)?.key === key) this.entries.delete(fingerprint);
  }

  get size(): number {
    return this.entries.size;
  }

  private prune(now: number): void {
    for (const [fingerprint, entry] of this.entries) {
      if (entry.expiresAt <= now) this.entries.delete(fingerprint);
    }
    // Map хранит порядок вставки — первыми вытесняются самые старые действия
    while (this.entries.size >= this.maxEntries) {
      const oldest = this.entries.keys().next();
      if (oldest.done) break;
      this.entries.delete(oldest.value);
    }
  }
}

/** Общий реестр приложения (client.ts). */
export const idempotencyKeys = new IdempotencyKeyRegistry();

export type MutationVerdict = 'done' | 'retry' | 'wait' | 'rotate';

/**
 * Что делать с ответом мутации:
 * `retry` — 502/503/504, повторить с тем же ключом;
 * `wait` — 409 `idempotency_in_progress` / `workflow_busy`, подождать по Retry-After;
 * `rotate` — 422 `idempotency_key_reused`, повторить один раз с новым ключом;
 * `done` — ответ окончательный (успех или обычная ошибка).
 */
export function classifyMutationResponse(status: number, code: string | null): MutationVerdict {
  if (status === 502 || status === 503 || status === 504) return 'retry';
  if (status === 409 && (code === 'idempotency_in_progress' || code === 'workflow_busy')) {
    return 'wait';
  }
  if (status === 422 && code === 'idempotency_key_reused') return 'rotate';
  return 'done';
}

function parseRetryAfterMs(value: string | null, now: number): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed) * 1000;
  const date = Date.parse(trimmed);
  return Number.isNaN(date) ? null : Math.max(0, date - now);
}

/**
 * Пауза перед повтором: по `Retry-After` (секунды или HTTP-дата) с ограничением 0,5..5 с,
 * без него — 500 / 1500 мс (по номеру попытки) ±20 %.
 */
export function retryDelayMs(
  attempt: number,
  retryAfter: string | null,
  random: () => number = Math.random,
): number {
  const fromHeader = parseRetryAfterMs(retryAfter, Date.now());
  if (fromHeader !== null) return Math.min(MAX_WAIT_MS, Math.max(MIN_WAIT_MS, fromHeader));
  const index = Math.min(Math.max(attempt, 1), BACKOFF_MS.length) - 1;
  return Math.round(BACKOFF_MS[index] * (0.8 + random() * 0.4));
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  );
}

function abortError(): Error {
  if (typeof DOMException !== 'undefined') {
    return new DOMException('Запрос отменён', 'AbortError');
  }
  const error = new Error('Запрос отменён');
  error.name = 'AbortError';
  return error;
}

/** Пауза, прерываемая AbortSignal (тогда — AbortError). */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export interface IdempotentMutationOptions {
  key: string;
  /** Одна попытка запроса с данным ключом (401-refresh — внутри). */
  attempt: (key: string) => Promise<Response>;
  /** Ошибка, после которой можно повторить (сеть). AbortError не повторяется никогда. */
  isRetryableError: (error: unknown) => boolean;
  /** Машинный код ошибки из тела ответа (читать через `response.clone()`). */
  readErrorCode: (response: Response) => Promise<string | null>;
  newKey: () => string;
  sleep?: (ms: number) => Promise<void>;
  signal?: AbortSignal;
  /** Попыток на сетевые ошибки и 502/503/504 (по умолчанию 3). */
  maxAttempts?: number;
  /** Суммарное ожидание in_progress / workflow_busy (по умолчанию 10 000 мс). */
  maxWaitMs?: number;
}

export interface IdempotentMutationResult {
  response: Response;
  /** Ключ последней попытки (после `rotate` — новый). */
  key: string;
  /** Ответ окончательный: ключ действия больше не нужен. */
  definitive: boolean;
}

/**
 * Выполняет мутацию с автоповтором под одним ключом.
 * Исчерпаны попытки по сети — бросается последняя ошибка (ключ остаётся в реестре:
 * ручное «Повторить» переиспользует его). Исчерпаны 5xx или ожидание — возвращается
 * последний ответ с `definitive: false`.
 */
export async function runIdempotentMutation(
  opts: IdempotentMutationOptions,
): Promise<IdempotentMutationResult> {
  const pause = opts.sleep ?? ((ms: number) => sleep(ms, opts.signal));
  const maxAttempts = Math.max(1, opts.maxAttempts ?? 3);
  const maxWaitMs = opts.maxWaitMs ?? 10_000;
  let key = opts.key;
  let failures = 0;
  let waitedMs = 0;
  let rotated = false;

  for (;;) {
    if (opts.signal?.aborted) throw abortError();
    let response: Response;
    try {
      response = await opts.attempt(key);
    } catch (error) {
      if (isAbortError(error) || !opts.isRetryableError(error)) throw error;
      failures += 1;
      if (failures >= maxAttempts) throw error;
      await pause(retryDelayMs(failures, null));
      continue;
    }
    if (response.ok) return { response, key, definitive: true };

    const verdict = classifyMutationResponse(response.status, await opts.readErrorCode(response));
    if (verdict === 'retry') {
      failures += 1;
      if (failures >= maxAttempts) return { response, key, definitive: false };
      await pause(retryDelayMs(failures, response.headers.get('Retry-After')));
      continue;
    }
    if (verdict === 'wait') {
      const delay = retryDelayMs(1, response.headers.get('Retry-After'));
      if (waitedMs + delay > maxWaitMs) return { response, key, definitive: false };
      waitedMs += delay;
      await pause(delay);
      continue;
    }
    if (verdict === 'rotate' && !rotated) {
      rotated = true;
      key = opts.newKey();
      continue;
    }
    return { response, key, definitive: true };
  }
}
