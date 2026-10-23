// Юнит-тесты контекста перехода (api-contract.md §4.3): что видел клиент и «уже выполнен».
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  isTransitionAlreadyDone,
  transitionExpectations,
} from '../src/shared/api/transitionContext.ts';

describe('transitionExpectations', () => {
  it('этап и ответственный из карточки', () => {
    assert.deepEqual(
      transitionExpectations({ status: { id: 's1' }, assignee: { id: 'u1' } }),
      {
        expected_status_id: 's1',
        expected_assignee_id: 'u1',
      },
    );
  });

  it('без ответственного — только этап (поле не отправляется)', () => {
    const seen = transitionExpectations({
      status: { id: 's1' },
      assignee: null,
    });
    assert.deepEqual(seen, { expected_status_id: 's1' });
    assert.equal('expected_assignee_id' in seen, false);
  });
});

describe('isTransitionAlreadyDone', () => {
  it('409 idempotency_replay_unavailable — переход уже выполнен', () => {
    assert.equal(
      isTransitionAlreadyDone({
        status: 409,
        code: 'idempotency_replay_unavailable',
      }),
      true,
    );
  });

  it('прочие ошибки — не выполнен', () => {
    for (const error of [
      { status: 409, code: 'stale_version' },
      { status: 409, code: 'workflow_invalid_transition' },
      { status: 422, code: 'idempotency_replay_unavailable' },
      new Error('network'),
      null,
      undefined,
      'строка',
    ]) {
      assert.equal(
        isTransitionAlreadyDone(error),
        false,
        String(JSON.stringify(error)),
      );
    }
  });
});
