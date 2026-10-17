// Юнит-тесты чистых форматтеров вкладки «Просрочки» (pages/reports/sla/slaFormat.ts):
// задержка и доли с малой выборкой, корзины старения, норма и её источник, подписи дельт
// и тренда, последнее действие, нагрузка относительно медианы, сортировка таблицы КАМов.
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  BUCKET_KEYS,
  bucketLabel,
  bucketOf,
  bucketTone,
  compareKamRows,
  counterpartyName,
  deltaCaption,
  deltaTone,
  formatDelay,
  formatPct,
  lastActionText,
  loadMark,
  mskDay,
  normText,
  onTimeCount,
  samplePct,
  trendLabel,
  trendTooltipTitle,
} from '../src/pages/reports/sla/slaFormat.ts';

const NBSP = ' ';

/** Строка КАМа с нулями — переопределяются нужные поля. */
function kam(overrides: Record<string, unknown>): Parameters<typeof compareKamRows>[0] {
  return {
    kam_id: 'k',
    kam: 'Иванов Иван Иванович',
    is_active: true,
    roles: ['kam'],
    active: 0,
    overdue: 0,
    own_overdue: 0,
    inherited: 0,
    overdue_pct: null,
    small_sample: false,
    delay_avg_days: null,
    delay_median_days: null,
    delay_max_days: null,
    delay_total_days: null,
    aging: { d1_3: 0, d4_7: 0, d8_14: 0, d15_30: 0, d31_plus: 0 },
    overdue_30_plus: 0,
    overdue_no_action: 0,
    decided: 0,
    on_time: 0,
    on_time_pct: null,
    completed: 0,
    late_closed: 0,
    late_inherited: 0,
    late_delay_avg_days: null,
    late_delay_median_days: null,
    inflow: 0,
    reminders: null,
    prev: null,
    delta: null,
    attention: false,
    ...overrides,
  } as Parameters<typeof compareKamRows>[0];
}

describe('formatDelay', () => {
  it('один знак, запятая, плюс, неразрывный пробел', () => {
    assert.equal(formatDelay(3.44), `+3,4${NBSP}дн.`);
    assert.equal(formatDelay(12), `+12${NBSP}дн.`);
    assert.equal(formatDelay(34.3), `+34,3${NBSP}дн.`);
  });
  it('null и NaN — прочерк', () => {
    assert.equal(formatDelay(null), '—');
    assert.equal(formatDelay(undefined), '—');
    assert.equal(formatDelay(Number.NaN), '—');
  });
  it('чуть больше 3 суток округляется до «+3 дн.», хотя корзина уже 4–7', () => {
    const delay = 3 + 1 / 86400;
    assert.equal(formatDelay(delay), `+3${NBSP}дн.`);
    assert.equal(bucketOf(delay), 'd4_7');
  });
});

describe('formatPct / samplePct', () => {
  it('null — прочерк, дробь — с запятой', () => {
    assert.equal(formatPct(null), '—');
    assert.equal(formatPct(57.14), `57,1${NBSP}%`);
    assert.equal(formatPct(20), `20${NBSP}%`);
    assert.equal(formatPct(0), `0${NBSP}%`);
  });
  it('малая выборка — значение остаётся, помечается', () => {
    const small = samplePct(100, 1);
    assert.equal(small.text, `100${NBSP}%`);
    assert.equal(small.small, true);
    assert.match(small.hint ?? '', /мало заявок/);
    assert.equal(samplePct(57.1, 7).small, false);
    assert.equal(samplePct(null, 0).small, false);
    assert.equal(samplePct(null, 0).text, '—');
  });
});

describe('корзины', () => {
  it('порядок и подписи', () => {
    assert.deepEqual([...BUCKET_KEYS], ['d1_3', 'd4_7', 'd8_14', 'd15_30', 'd31_plus']);
    assert.equal(bucketLabel('d1_3'), '1–3 дн.');
    assert.equal(bucketLabel('d31_plus'), 'больше 30 дн.');
  });
  it('границы по целым суткам вверх', () => {
    assert.equal(bucketOf(3), 'd1_3');
    assert.equal(bucketOf(7), 'd4_7');
    assert.equal(bucketOf(14), 'd8_14');
    assert.equal(bucketOf(30), 'd15_30');
    assert.equal(bucketOf(30 + 1 / 86400), 'd31_plus');
  });
  it('тон: до недели — warning, дольше — danger', () => {
    assert.equal(bucketTone('d1_3'), 'warning');
    assert.equal(bucketTone('d4_7'), 'warning');
    assert.equal(bucketTone('d8_14'), 'danger');
    assert.equal(bucketTone('d31_plus'), 'danger');
  });
});

describe('normText', () => {
  it('норма и источник', () => {
    assert.equal(normText(14, 'workflow'), `норма 14${NBSP}дн. (общая для процесса)`);
    assert.equal(normText(7, 'stage'), `норма 7${NBSP}дн. (задана для этапа)`);
    assert.equal(normText(14, 'global'), `норма 14${NBSP}дн. (общая настройка)`);
  });
});

describe('deltaCaption / deltaTone', () => {
  it('подпись, к чему сравнение', () => {
    assert.equal(deltaCaption(4, 'count', 'к 01.10'), '+4 к 01.10');
    assert.equal(deltaCaption(-3.7, 'pct', 'к 15.09–30.09'), `−3,7${NBSP}п.п. к 15.09–30.09`);
    assert.equal(deltaCaption(4.2, 'days', 'к 01.10'), `+4,2${NBSP}дн. к 01.10`);
    assert.equal(deltaCaption(0, 'count', 'к 01.10'), 'без изменений к 01.10');
    assert.equal(deltaCaption(null, 'count', 'к 01.10'), null);
  });
  it('рост просрочек — плохо, рост «уложились» — хорошо, объёмы — нейтрально', () => {
    assert.equal(deltaTone(4, 'inverted'), 'bad');
    assert.equal(deltaTone(-4, 'inverted'), 'good');
    assert.equal(deltaTone(5, 'normal'), 'good');
    assert.equal(deltaTone(5, 'neutral'), 'neutral');
    assert.equal(deltaTone(0, 'inverted'), 'neutral');
    assert.equal(deltaTone(null, 'inverted'), 'neutral');
  });
});

describe('compareKamRows', () => {
  const a = kam({ kam_id: 'a', kam: 'Андреев', own_overdue: 4, overdue: 4, overdue_pct: 57.1, active: 7 });
  const b = kam({ kam_id: 'b', kam: 'Борисов', own_overdue: 2, overdue: 5, inherited: 3, overdue_pct: 50, active: 10 });
  const c = kam({ kam_id: 'c', kam: 'Васильев', own_overdue: 2, overdue: 2, overdue_pct: 66.7, active: 3 });
  const d = kam({ kam_id: 'd', kam: 'Григорьев', own_overdue: 0, on_time_pct: null });

  it('по умолчанию — собственные просрочки ↓, затем доля ↓ (унаследованные не поднимают)', () => {
    const order = [d, b, c, a].sort((x, y) => compareKamRows(x, y)).map((row) => row.kam_id);
    assert.deepEqual(order, ['a', 'c', 'b', 'd']);
  });
  it('null — в конце при любом направлении', () => {
    const e = kam({ kam_id: 'e', kam: 'Егоров', on_time_pct: 40 });
    const f = kam({ kam_id: 'f', kam: 'Жуков', on_time_pct: 90 });
    const desc = [d, e, f].sort((x, y) => compareKamRows(x, y, 'on_time_pct', 'desc')).map((r) => r.kam_id);
    const asc = [d, e, f].sort((x, y) => compareKamRows(x, y, 'on_time_pct', 'asc')).map((r) => r.kam_id);
    assert.deepEqual(desc, ['f', 'e', 'd']);
    assert.deepEqual(asc, ['e', 'f', 'd']);
  });
  it('по имени — по алфавиту', () => {
    const order = [c, a, b].sort((x, y) => compareKamRows(x, y, 'kam', 'asc')).map((r) => r.kam_id);
    assert.deepEqual(order, ['a', 'b', 'c']);
  });
});

describe('тренд', () => {
  it('подпись — дата среза из API, без неё — snapshot_at − 1 с по МСК', () => {
    assert.equal(trendLabel({ label: '23.08', snapshot_at: '2026-08-23T21:00:00Z' }), '23.08');
    // понедельник 24.08 00:00 МСК = 23.08 21:00 UTC → срез «на конец воскресенья» 23.08
    assert.equal(trendLabel({ label: '', snapshot_at: '2026-08-23T21:00:00Z' }), '23.08');
  });
  it('незаконченная точка помечена', () => {
    const title = trendTooltipTitle(
      { label: '16.10', snapshot_at: '2026-10-16T09:00:00Z', complete: false },
      'week',
    );
    assert.equal(title, 'на 16.10 · неделя не закончилась');
    assert.equal(
      trendTooltipTitle({ label: 'на 30.09', snapshot_at: '2026-09-30T21:00:00Z', complete: true }, 'month'),
      'на 30.09',
    );
  });
});

describe('lastActionText', () => {
  it('когда · что · кто', () => {
    assert.equal(
      lastActionText({
        at: '2026-10-04T10:00:00Z',
        kind: 'comment',
        actor_id: 'u',
        actor: 'Павлов Роман Евгеньевич',
        days_ago: 12,
      }),
      `12${NBSP}дн. назад · комментарий · Павлов Р. Е.`,
    );
    assert.equal(
      lastActionText({ at: '2026-10-16T08:00:00Z', kind: 'transition', actor_id: null, actor: null, days_ago: 0.2 }),
      'сегодня · переход',
    );
    assert.equal(lastActionText(null), 'действий в системе не было');
  });
});

describe('loadMark', () => {
  it('выше / ниже / на уровне медианы', () => {
    assert.equal(loadMark(12, 7), 'above');
    assert.equal(loadMark(3, 7), 'below');
    assert.equal(loadMark(7, 7), 'median');
    assert.equal(loadMark(7, null), null);
  });
});

describe('прочее', () => {
  it('вуз или клиент', () => {
    assert.equal(counterpartyName({ id: '1', name: 'МЭИ полное', short_name: 'МЭИ' }, null), 'МЭИ');
    assert.equal(counterpartyName(null, 'Петров П. П.'), 'Петров П. П.');
    assert.equal(counterpartyName(null, ''), '—');
    assert.equal(counterpartyName(null, null), '—');
  });
  it('уложились: поле API или доля × решённых', () => {
    assert.equal(onTimeCount({ decided: 14, on_time_pct: 57.1, on_time: 8 }), 8);
    assert.equal(onTimeCount({ decided: 14, on_time_pct: 57.1 }), 8);
    assert.equal(onTimeCount({ decided: 0, on_time_pct: null }), 0);
  });
  it('дата по МСК', () => {
    assert.equal(mskDay('2026-10-16'), '16.10.2026');
    assert.equal(mskDay('2026-10-15T21:30:00Z'), '16.10.2026');
    assert.equal(mskDay(null), '—');
  });
});
