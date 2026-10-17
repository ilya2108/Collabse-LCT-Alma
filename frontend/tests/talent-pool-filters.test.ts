// Юнит-тесты стартовых фильтров реестра talent pool (pages/talent-pool/registryFilters.ts):
// ссылка «Студенты вуза (N)» из карточки вуза (`?university=<id>`) фильтрует реестр по вузу.
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  registryDefaultFilters,
  universityParamId,
} from '../src/pages/talent-pool/registryFilters.ts';

const UNIVERSITY = '3f2504e0-4f89-41d3-9a0c-0305e82c3301';

describe('universityParamId', () => {
  it('принимает UUID и обрезает пробелы', () => {
    assert.equal(universityParamId(UNIVERSITY), UNIVERSITY);
    assert.equal(universityParamId(` ${UNIVERSITY.toUpperCase()} `), UNIVERSITY.toUpperCase());
  });

  it('отбрасывает пустое и не-UUID — сервер ответил бы 422', () => {
    assert.equal(universityParamId(null), null);
    assert.equal(universityParamId(''), null);
    assert.equal(universityParamId('mgu'), null);
    assert.equal(universityParamId(`${UNIVERSITY}x`), null);
  });
});

describe('registryDefaultFilters', () => {
  it('без параметров — без фильтров', () => {
    assert.deepEqual(registryDefaultFilters(null, null), {});
  });

  it('ссылка из карточки вуза задаёт фильтр university_id', () => {
    assert.deepEqual(registryDefaultFilters(null, UNIVERSITY), { university_id: UNIVERSITY });
  });

  it('сегмент воронки и вуз сочетаются', () => {
    assert.deepEqual(registryDefaultFilters('studying', UNIVERSITY), {
      funnel_status: 'studying',
      university_id: UNIVERSITY,
    });
  });

  it('мусор в university не превращается в фильтр', () => {
    assert.deepEqual(registryDefaultFilters('graduate', 'abc'), { funnel_status: 'graduate' });
  });
});
