// Юнит-тесты карточки «Сигналы» на «Аналитике» (pages/dashboard/signalsModel.ts): тон по
// порогам (ноль — всегда спокойно), подписи плиток и строк окна, процесс для перехода на
// доску, подпись для экранного диктора.
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  SIGNAL_THRESHOLDS,
  countTone,
  daysLeftText,
  licenseCaption,
  licenseTone,
  licenseValue,
  plural,
  signalAriaLabel,
  signalValueText,
  stuckCaption,
  targetWorkflow,
  toneLabel,
  transferStatusLabel,
  unassignedCaption,
  untouchedCaption,
  workflowBreakdown,
} from '../src/pages/dashboard/signalsModel.ts';

const PERIOD = { from: '2026-09-18', to: '2026-10-17', as_of: '2026-10-17T09:00:00+03:00', days: 30 };

describe('тон', () => {
  it('ноль — всегда «норма», даже при нулевом пороге', () => {
    assert.equal(countTone(0, SIGNAL_THRESHOLDS.stuck), 'calm');
    assert.equal(countTone(0, { warning: 0, critical: 0 }), 'calm');
    assert.equal(licenseTone({ expiring: 0, expired: 0 }), 'calm');
  });

  it('пороги «внимание» и «критично» включительно', () => {
    const { warning, critical } = SIGNAL_THRESHOLDS.stuck;
    assert.equal(countTone(warning, SIGNAL_THRESHOLDS.stuck), 'warning');
    assert.equal(countTone(critical - 1, SIGNAL_THRESHOLDS.stuck), 'warning');
    assert.equal(countTone(critical, SIGNAL_THRESHOLDS.stuck), 'critical');
    assert.equal(countTone(2, SIGNAL_THRESHOLDS.unassigned), 'warning');
    assert.equal(countTone(5, SIGNAL_THRESHOLDS.unassigned), 'critical');
    assert.equal(countTone(11, SIGNAL_THRESHOLDS.untouched), 'warning');
  });

  it('лицензии: истёкшая — критично, только истекающие — внимание', () => {
    assert.equal(licenseTone({ expiring: 3, expired: 1 }), 'critical');
    assert.equal(licenseTone({ expiring: 0, expired: 1 }), 'critical');
    assert.equal(licenseTone({ expiring: 2, expired: 0 }), 'warning');
  });

  it('тон словом — не только цветом', () => {
    assert.deepEqual(
      (['calm', 'warning', 'critical'] as const).map(toneLabel),
      ['норма', 'внимание', 'критично'],
    );
  });
});

describe('подписи плиток', () => {
  it('ноль — «нет»', () => {
    assert.equal(signalValueText(0), 'нет');
    assert.equal(signalValueText(22), '22');
  });

  it('зависшие: склонение и разбивка по процессам только при двух процессах', () => {
    assert.equal(
      stuckCaption({ value: 22, by_workflow: { b2b: 19, b2c: 3 } }),
      'заявки стоят на этапе дольше нормы · B2B 19 · B2C 3',
    );
    assert.equal(
      stuckCaption({ value: 1, by_workflow: { b2b: 1, b2c: 0 } }),
      'заявка стоит на этапе дольше нормы',
    );
    assert.equal(
      stuckCaption({ value: 5, by_workflow: { b2c: 5 } }),
      'заявок стоят на этапе дольше нормы',
    );
    assert.equal(
      stuckCaption({ value: 0, by_workflow: { b2b: 0, b2c: 0 } }),
      'Все открытые заявки в пределах нормы этапа',
    );
  });

  it('без ответственного КАМа', () => {
    assert.equal(
      unassignedCaption({ value: 2, by_workflow: { b2b: 0, b2c: 2 } }),
      'Ответственный — не КАМ процесса или деактивирован',
    );
    assert.equal(
      unassignedCaption({ value: 0, by_workflow: {} }),
      'Все открытые заявки закреплены за КАМами',
    );
  });

  it('лицензии: число — истекают и истекли вместе', () => {
    const signal = { expiring: 3, expired: 1, horizon_days: 30 };
    assert.equal(licenseValue(signal), 4);
    assert.equal(licenseCaption(signal), 'Истекают за 30 дн.: 3 · истекли: 1');
    assert.equal(
      licenseCaption({ expiring: 0, expired: 0, horizon_days: 30 }),
      'В ближайшие 30 дней лицензии не истекают',
    );
  });

  it('вузы без активности — с длиной периода', () => {
    assert.equal(
      untouchedCaption({ value: 11, period: PERIOD }),
      'Закреплены за КАМами, без взаимодействий за 30 дн.',
    );
    assert.equal(
      untouchedCaption({ value: 0, period: PERIOD }),
      'Со всеми закреплёнными вузами был контакт за 30 дн.',
    );
  });

  it('разбивка по процессам', () => {
    assert.equal(workflowBreakdown({ b2b: 19, b2c: 3 }), 'B2B 19 · B2C 3');
    assert.equal(workflowBreakdown({ b2b: 4, b2c: 0 }), '');
    assert.equal(workflowBreakdown({}), '');
  });

  it('подпись для экранного диктора', () => {
    assert.equal(
      signalAriaLabel('Зависшие заявки', 0, 'calm', 'Все открытые заявки в пределах нормы этапа', 'Открыть доску'),
      'Зависшие заявки: нет, норма. Все открытые заявки в пределах нормы этапа. Открыть доску',
    );
    // пояснение с маленькой буквы и с «дн.» в конце — одно предложение без двойной точки
    assert.equal(
      signalAriaLabel('Зависшие заявки', 22, 'critical', 'заявки стоят на этапе дольше нормы', 'Открыть доску'),
      'Зависшие заявки: 22, критично. Заявки стоят на этапе дольше нормы. Открыть доску',
    );
    assert.equal(
      signalAriaLabel('Вузы без активности', 3, 'warning', 'Без взаимодействий за 30 дн.', 'Открыть список'),
      'Вузы без активности: 3, внимание. Без взаимодействий за 30 дн. Открыть список',
    );
  });
});

describe('строки окна лицензий', () => {
  it('срок: граница «сегодня», будущее и прошлое со склонением', () => {
    assert.equal(daysLeftText(0), 'последний день — сегодня');
    assert.equal(daysLeftText(1), 'через 1 день');
    assert.equal(daysLeftText(3), 'через 3 дня');
    assert.equal(daysLeftText(29), 'через 29 дней');
    assert.equal(daysLeftText(30), 'через 30 дней');
    assert.equal(daysLeftText(-1), 'истекла 1 день назад');
    assert.equal(daysLeftText(-36), 'истекла 36 дней назад');
  });

  it('статус передачи', () => {
    assert.equal(transferStatusLabel('transferred'), 'передана вузу');
    assert.equal(transferStatusLabel('in_progress'), 'передача в работе');
    assert.equal(transferStatusLabel('not_started'), 'передача не начата');
    assert.equal(transferStatusLabel(null), 'статус передачи не указан');
  });

  it('склонение', () => {
    assert.deepEqual(
      [1, 2, 5, 11, 21, 22, 112].map((n) => plural(n, ['день', 'дня', 'дней'])),
      ['день', 'дня', 'дней', 'дней', 'день', 'дня', 'дней'],
    );
  });
});

describe('переход на доску', () => {
  it('выбранный процесс важнее', () => {
    assert.equal(targetWorkflow({ b2b: 5, b2c: 1 }, 'b2c', ['b2b', 'b2c']), 'b2c');
  });

  it('«Все» — первый процесс, где сигнал есть', () => {
    assert.equal(targetWorkflow({ b2b: 0, b2c: 2 }, null, ['b2b', 'b2c']), 'b2c');
    assert.equal(targetWorkflow({ b2b: 3, b2c: 2 }, null, ['b2b', 'b2c']), 'b2b');
  });

  it('сигнала нет — первый доступный роли', () => {
    assert.equal(targetWorkflow({ b2b: 0, b2c: 0 }, null, ['b2c']), 'b2c');
    assert.equal(targetWorkflow({}, null, []), 'b2b');
  });

  it('процесс вне области роли не выбирается', () => {
    assert.equal(targetWorkflow({ b2b: 4, b2c: 0 }, null, ['b2c']), 'b2c');
  });
});
