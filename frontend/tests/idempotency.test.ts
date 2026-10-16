// Юнит-тесты клиентских ключей идемпотентности (api-contract.md §1.6).
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  IdempotencyKeyRegistry,
  MUTATING_METHODS,
  classifyMutationResponse,
  newIdempotencyKey,
  requestFingerprint,
  retryDelayMs,
  runIdempotentMutation,
  sleep,
  stableStringify,
} from '../src/shared/api/idempotency.ts';

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function errorResponse(status: number, code: string, headers: Record<string, string> = {}): Response {
  return jsonResponse(status, { error: { code, message: 'ошибка', trace_id: null } }, headers);
}

async function readErrorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.clone().json()) as { error?: { code?: string } };
    return body.error?.code ?? null;
  } catch {
    return null;
  }
}

class NetworkError extends Error {
  readonly code = 'network_error';
}

/** Сценарий ответов: функция или Response/Error на каждую попытку. */
function scripted(steps: (Response | Error)[]) {
  const keys: string[] = [];
  const attempt = async (key: string): Promise<Response> => {
    keys.push(key);
    const step = steps[keys.length - 1];
    if (step === undefined) throw new Error(`лишняя попытка №${keys.length}`);
    if (step instanceof Error) throw step;
    return step;
  };
  return { keys, attempt };
}

function baseOptions(attempt: (key: string) => Promise<Response>, sleeps: number[]) {
  let rotated = 0;
  return {
    key: 'key-1',
    attempt,
    isRetryableError: (error: unknown) => error instanceof NetworkError,
    readErrorCode,
    newKey: () => `rotated-${++rotated}`,
    sleep: async (ms: number) => {
      sleeps.push(ms);
    },
  };
}

describe('stableStringify', () => {
  it('сортирует ключи рекурсивно и опускает undefined', () => {
    const value = { b: 1, a: { d: [3, { y: 1, x: 2 }], c: undefined }, e: null };
    assert.equal(stableStringify(value), '{"a":{"d":[3,{"x":2,"y":1}]},"b":1,"e":null}');
  });

  it('одинаковые объекты с разным порядком ключей совпадают', () => {
    assert.equal(stableStringify({ x: 1, y: [1, 2] }), stableStringify({ y: [1, 2], x: 1 }));
  });

  it('учитывает toJSON (даты)', () => {
    const at = new Date('2026-10-17T10:00:00Z');
    assert.equal(stableStringify({ at }), '{"at":"2026-10-17T10:00:00.000Z"}');
  });
});

describe('requestFingerprint', () => {
  it('JSON с разным порядком ключей — один отпечаток', () => {
    const a = requestFingerprint('post', '/api/v1/requests', { title: 'A', amount: 1 });
    const b = requestFingerprint('POST', '/api/v1/requests', { amount: 1, title: 'A' });
    assert.equal(a, b);
  });

  it('метод, URL и тело различают действия', () => {
    const base = requestFingerprint('POST', '/api/v1/requests', { title: 'A' });
    assert.notEqual(base, requestFingerprint('PUT', '/api/v1/requests', { title: 'A' }));
    assert.notEqual(base, requestFingerprint('POST', '/api/v1/requests?x=1', { title: 'A' }));
    assert.notEqual(base, requestFingerprint('POST', '/api/v1/requests', { title: 'B' }));
    assert.notEqual(base, requestFingerprint('POST', '/api/v1/requests', undefined));
  });

  it('FormData с тем же файлом (имя, размер, lastModified) совпадает', () => {
    const make = (content: string) => {
      const form = new FormData();
      form.append('kind', 'universities');
      form.append('file', new File([content], 'vuzy.xlsx', { type: 'application/x', lastModified: 42 }));
      return form;
    };
    const first = requestFingerprint('POST', '/api/v1/import/sessions', make('abc'));
    assert.equal(first, requestFingerprint('POST', '/api/v1/import/sessions', make('xyz')));
    assert.notEqual(first, requestFingerprint('POST', '/api/v1/import/sessions', make('abcd')));
  });
});

describe('IdempotencyKeyRegistry', () => {
  it('acquire дважды — тот же ключ; settle — новый', () => {
    let n = 0;
    const registry = new IdempotencyKeyRegistry({ newKey: () => `k${++n}` });
    assert.equal(registry.acquire('fp'), 'k1');
    assert.equal(registry.acquire('fp'), 'k1');
    assert.equal(registry.acquire('other'), 'k2');
    registry.settle('fp', 'k1');
    assert.equal(registry.acquire('fp'), 'k3');
  });

  it('settle с чужим ключом ничего не делает', () => {
    let n = 0;
    const registry = new IdempotencyKeyRegistry({ newKey: () => `k${++n}` });
    registry.acquire('fp');
    registry.settle('fp', 'stale');
    assert.equal(registry.acquire('fp'), 'k1');
  });

  it('ключ живёт TTL', () => {
    let now = 1_000;
    let n = 0;
    const registry = new IdempotencyKeyRegistry({
      ttlMs: 100,
      now: () => now,
      newKey: () => `k${++n}`,
    });
    assert.equal(registry.acquire('fp'), 'k1');
    now += 99;
    assert.equal(registry.acquire('fp'), 'k1');
    now += 1;
    assert.equal(registry.acquire('fp'), 'k2');
  });

  it('не больше maxEntries: вытесняются старые', () => {
    let n = 0;
    const registry = new IdempotencyKeyRegistry({ maxEntries: 2, newKey: () => `k${++n}` });
    registry.acquire('a');
    registry.acquire('b');
    registry.acquire('c');
    assert.equal(registry.size, 2);
    assert.equal(registry.acquire('b'), 'k2');
    assert.equal(registry.acquire('a'), 'k4');
  });

  it('ключ по умолчанию — UUID v4', () => {
    const key = new IdempotencyKeyRegistry().acquire('fp');
    assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.match(newIdempotencyKey(), /^[0-9a-f-]{36}$/);
  });
});

describe('classifyMutationResponse', () => {
  it('по вердикту на случай', () => {
    assert.equal(classifyMutationResponse(201, null), 'done');
    assert.equal(classifyMutationResponse(400, 'validation_error'), 'done');
    assert.equal(classifyMutationResponse(409, 'stale_version'), 'done');
    assert.equal(classifyMutationResponse(503, 'service_unavailable'), 'retry');
    assert.equal(classifyMutationResponse(502, null), 'retry');
    assert.equal(classifyMutationResponse(504, null), 'retry');
    assert.equal(classifyMutationResponse(409, 'idempotency_in_progress'), 'wait');
    assert.equal(classifyMutationResponse(409, 'workflow_busy'), 'wait');
    assert.equal(classifyMutationResponse(422, 'idempotency_key_reused'), 'rotate');
    assert.equal(classifyMutationResponse(422, 'unprocessable'), 'done');
  });

  it('мутирующие методы', () => {
    assert.deepEqual([...MUTATING_METHODS].sort(), ['DELETE', 'PATCH', 'POST', 'PUT']);
  });
});

describe('retryDelayMs', () => {
  it('без Retry-After — 500 / 1500 мс ±20 %', () => {
    assert.equal(retryDelayMs(1, null, () => 0.5), 500);
    assert.equal(retryDelayMs(2, null, () => 0.5), 1500);
    assert.equal(retryDelayMs(1, null, () => 0), 400);
    assert.equal(retryDelayMs(2, null, () => 1), 1800);
    assert.equal(retryDelayMs(9, null, () => 0.5), 1500);
  });

  it('Retry-After — с ограничением 0,5..5 с', () => {
    assert.equal(retryDelayMs(1, '1'), 1000);
    assert.equal(retryDelayMs(1, '2'), 2000);
    assert.equal(retryDelayMs(1, '0'), 500);
    assert.equal(retryDelayMs(1, '120'), 5000);
    const inFuture = new Date(Date.now() + 60_000).toUTCString();
    assert.equal(retryDelayMs(1, inFuture), 5000);
    assert.equal(retryDelayMs(1, 'мусор', () => 0.5), 500);
  });
});

describe('runIdempotentMutation', () => {
  it('сетевая ошибка, затем 201 — обе попытки с одним ключом, definitive', async () => {
    const sleeps: number[] = [];
    const { keys, attempt } = scripted([new NetworkError('offline'), jsonResponse(201, { id: 1 })]);
    const result = await runIdempotentMutation(baseOptions(attempt, sleeps));
    assert.deepEqual(keys, ['key-1', 'key-1']);
    assert.equal(result.response.status, 201);
    assert.equal(result.definitive, true);
    assert.equal(result.key, 'key-1');
    assert.equal(sleeps.length, 1);
    assert.ok(sleeps[0] >= 400 && sleeps[0] <= 600);
  });

  it('503 трижды — три попытки, не definitive', async () => {
    const sleeps: number[] = [];
    const { keys, attempt } = scripted([
      errorResponse(503, 'service_unavailable'),
      errorResponse(503, 'service_unavailable'),
      errorResponse(503, 'service_unavailable'),
    ]);
    const result = await runIdempotentMutation(baseOptions(attempt, sleeps));
    assert.deepEqual(keys, ['key-1', 'key-1', 'key-1']);
    assert.equal(result.response.status, 503);
    assert.equal(result.definitive, false);
    assert.equal(sleeps.length, 2);
  });

  it('сеть недоступна все попытки — бросается последняя ошибка', async () => {
    const { keys, attempt } = scripted([
      new NetworkError('1'),
      new NetworkError('2'),
      new NetworkError('3'),
    ]);
    await assert.rejects(runIdempotentMutation(baseOptions(attempt, [])), /^Error: 3$/);
    assert.equal(keys.length, 3);
  });

  it('409 in_progress с Retry-After: 1 — пауза 1000 мс, затем 201', async () => {
    const sleeps: number[] = [];
    const { keys, attempt } = scripted([
      errorResponse(409, 'idempotency_in_progress', { 'Retry-After': '1' }),
      jsonResponse(201, { id: 1 }),
    ]);
    const result = await runIdempotentMutation(baseOptions(attempt, sleeps));
    assert.deepEqual(sleeps, [1000]);
    assert.deepEqual(keys, ['key-1', 'key-1']);
    assert.equal(result.definitive, true);
  });

  it('409 workflow_busy — ожидание суммарно не дольше 10 с', async () => {
    const sleeps: number[] = [];
    const busy = () => errorResponse(409, 'workflow_busy', { 'Retry-After': '2' });
    const { keys, attempt } = scripted(Array.from({ length: 10 }, busy));
    const result = await runIdempotentMutation(baseOptions(attempt, sleeps));
    assert.deepEqual(sleeps, [2000, 2000, 2000, 2000, 2000]);
    assert.equal(keys.length, 6);
    assert.equal(result.response.status, 409);
    assert.equal(result.definitive, false);
  });

  it('422 reused — одна смена ключа', async () => {
    const { keys, attempt } = scripted([
      errorResponse(422, 'idempotency_key_reused'),
      errorResponse(422, 'idempotency_key_reused'),
    ]);
    const result = await runIdempotentMutation(baseOptions(attempt, []));
    assert.deepEqual(keys, ['key-1', 'rotated-1']);
    assert.equal(result.key, 'rotated-1');
    assert.equal(result.response.status, 422);
    assert.equal(result.definitive, true);
  });

  it('422 reused, затем 201 — успех с новым ключом', async () => {
    const { keys, attempt } = scripted([
      errorResponse(422, 'idempotency_key_reused'),
      jsonResponse(201, { id: 2 }),
    ]);
    const result = await runIdempotentMutation(baseOptions(attempt, []));
    assert.deepEqual(keys, ['key-1', 'rotated-1']);
    assert.equal(result.response.status, 201);
  });

  it('400 — без повтора, definitive', async () => {
    const { keys, attempt } = scripted([errorResponse(400, 'validation_error')]);
    const result = await runIdempotentMutation(baseOptions(attempt, []));
    assert.equal(keys.length, 1);
    assert.equal(result.response.status, 400);
    assert.equal(result.definitive, true);
  });

  it('AbortError пробрасывается сразу', async () => {
    const abort = new DOMException('The operation was aborted.', 'AbortError');
    const { keys, attempt } = scripted([abort, jsonResponse(201, {})]);
    const options = { ...baseOptions(attempt, []), isRetryableError: () => true };
    await assert.rejects(runIdempotentMutation(options), { name: 'AbortError' });
    assert.equal(keys.length, 1);
  });

  it('отмена во время паузы прерывает ожидание', async () => {
    const controller = new AbortController();
    const { keys, attempt } = scripted([
      errorResponse(503, 'service_unavailable'),
      jsonResponse(201, {}),
    ]);
    const options = { ...baseOptions(attempt, []), signal: controller.signal };
    delete (options as { sleep?: unknown }).sleep;
    const pending = runIdempotentMutation(options);
    setTimeout(() => controller.abort(), 10);
    await assert.rejects(pending, { name: 'AbortError' });
    assert.equal(keys.length, 1);
  });
});

describe('sleep', () => {
  it('уже отменённый сигнал — сразу AbortError', async () => {
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(sleep(1000, controller.signal), { name: 'AbortError' });
  });

  it('без сигнала — просто пауза', async () => {
    const started = Date.now();
    await sleep(5);
    assert.ok(Date.now() - started >= 4);
  });
});
