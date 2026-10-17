// Юнит-тесты чистых функций «Сводки ИИ» (pages/reports/ai/aiSummaryModel.ts): статусы и
// интервалы опроса, строки прогресса со склонениями, подписи источника и сверки чисел,
// тексты сбоев по кодам задачи, порядок секций, текст для буфера обмена, сравнение фильтров
// и тело запроса для команды, КАМа и «только свои».
// Запуск: npm run test:unit (node --test с удалением типов, Node ≥ 22.6).
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  POLL_LIMIT_MS,
  checksCaption,
  errorView,
  formatElapsed,
  isLongQueue,
  isPending,
  isPollExpired,
  isStale,
  mskDateTime,
  pageTarget,
  paramsKey,
  periodLabel,
  plural,
  pollInterval,
  progressText,
  requestBody,
  sectionList,
  sourceCaption,
  summaryTitle,
  toPlainText,
} from '../src/pages/reports/ai/aiSummaryModel.ts';

type Job = Parameters<typeof toPlainText>[0];

const TEAM_PARAMS = {
  from: '2026-09-16',
  to: '2026-10-16',
  workflow_type: null,
  kam_id: [],
  university_id: [],
  direction_id: [],
};

/** Задача с разумными значениями — переопределяются нужные поля. */
function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'job-1',
    kind: 'team',
    kam_id: null,
    kam_name: null,
    status: 'done',
    mode: 'auto',
    provider: 'gigachat',
    params: TEAM_PARAMS,
    created_at: '2026-10-16T11:02:03+00:00',
    started_at: '2026-10-16T11:02:03+00:00',
    finished_at: '2026-10-16T11:03:08+00:00',
    queue_ahead: null,
    poll_after_ms: null,
    reused: false,
    result: {
      source: 'gigachat',
      model: 'GigaChat-2-Pro',
      generated_at: '2026-10-16T11:03:08+00:00',
      sections: {
        headline: 'На 16.10.2026 в работе 47 заявок, просрочено 9 (19,1%).',
        going_well: ['Уложились в норму: 71,4% решённых этапов.'],
        lagging: ['Больше всего просрочек на этапе «Согласование договора» (B2B): 3 из 8.'],
        attention: ['Иванов И. И.: собственных просрочек 4 из 12 (33,3%).'],
        questions: ['Что задерживает заявки на этапе «Согласование договора»?'],
      },
      people: [{ alias: 'КАМ-03', kam_id: 'a1', name: 'Иванов И. И.' }],
      checks: { numbers_total: 18, numbers_matched: 18, sentences_removed: 0, advice_removed: 0 },
    },
    error: null,
    ...overrides,
  };
}

describe('isPending', () => {
  it('queued и running — в работе, остальные — нет', () => {
    assert.equal(isPending('queued'), true);
    assert.equal(isPending('running'), true);
    assert.equal(isPending('done'), false);
    assert.equal(isPending('failed'), false);
    assert.equal(isPending(null), false);
    assert.equal(isPending(undefined), false);
  });
});

describe('pollInterval', () => {
  const queued = job({ status: 'queued', result: null, poll_after_ms: 2000 });

  it('2 с первую минуту, затем 4 с', () => {
    assert.equal(pollInterval(queued, 0), 2000);
    assert.equal(pollInterval(queued, 59_999), 2000);
    assert.equal(pollInterval(queued, 60_000), 4000);
    assert.equal(pollInterval({ ...queued, status: 'running' }, 120_000), 4000);
  });

  it('без poll_after_ms — 2 с; больший интервал сервера сохраняется', () => {
    assert.equal(pollInterval({ ...queued, poll_after_ms: null }, 1000), 2000);
    assert.equal(pollInterval({ ...queued, poll_after_ms: 5000 }, 1000), 5000);
    assert.equal(pollInterval({ ...queued, poll_after_ms: 5000 }, 90_000), 5000);
  });

  it('терминальные статусы, нет задачи и предел 15 минут — без опроса', () => {
    assert.equal(pollInterval(job(), 1000), false);
    assert.equal(pollInterval(job({ status: 'failed', result: null }), 1000), false);
    assert.equal(pollInterval(null, 1000), false);
    assert.equal(pollInterval(undefined, 1000), false);
    assert.equal(pollInterval(queued, POLL_LIMIT_MS), false);
    assert.equal(POLL_LIMIT_MS, 15 * 60_000);
  });

  it('isPollExpired — только для незавершённой задачи после предела', () => {
    assert.equal(isPollExpired(queued, POLL_LIMIT_MS - 1), false);
    assert.equal(isPollExpired(queued, POLL_LIMIT_MS), true);
    assert.equal(isPollExpired(job(), POLL_LIMIT_MS * 2), false);
    assert.equal(isPollExpired(null, POLL_LIMIT_MS * 2), false);
  });
});

describe('plural и formatElapsed', () => {
  it('склонения: 1 сводка, 2 сводки, 5 сводок, 11–14 сводок, 21 сводка', () => {
    const forms: [string, string, string] = ['сводка', 'сводки', 'сводок'];
    assert.equal(plural(1, forms), 'сводка');
    assert.equal(plural(2, forms), 'сводки');
    assert.equal(plural(4, forms), 'сводки');
    assert.equal(plural(5, forms), 'сводок');
    assert.equal(plural(11, forms), 'сводок');
    assert.equal(plural(14, forms), 'сводок');
    assert.equal(plural(21, forms), 'сводка');
    assert.equal(plural(22, forms), 'сводки');
    assert.equal(plural(111, forms), 'сводок');
  });

  it('секунды и минуты', () => {
    assert.equal(formatElapsed(0), '0 с');
    assert.equal(formatElapsed(23_400), '23 с');
    assert.equal(formatElapsed(65_000), '1 мин 5 с');
    assert.equal(formatElapsed(180_000), '3 мин');
    assert.equal(formatElapsed(-5), '0 с');
  });
});

describe('progressText', () => {
  const running = job({ status: 'running', result: null, poll_after_ms: 2000 });

  it('до ответа сервера — сбор данных', () => {
    assert.equal(progressText(null, 0), 'Собираем данные отчёта…');
  });

  it('формируется: секунды и подсказка о длительности', () => {
    assert.equal(progressText(running, 23_000), 'Формируется · 23 с · обычно до минуты');
    assert.match(progressText(running, 75_000), /^Формируется · 1 мин 15 с · иногда до 2–3 минут$/);
    assert.match(progressText(running, 200_000), /дольше обычного/);
  });

  it('очередь GigaChat — со склонением «сводка»', () => {
    const queued = { ...running, status: 'queued' as const };
    assert.equal(
      progressText({ ...queued, queue_ahead: 2 }, 5000),
      'В очереди: перед вами 2 сводки — GigaChat обрабатывает по одной · 5 с',
    );
    assert.match(progressText({ ...queued, queue_ahead: 1 }, 0), /перед вами 1 сводка —/);
    assert.match(progressText({ ...queued, queue_ahead: 5 }, 0), /перед вами 5 сводок —/);
    assert.match(progressText({ ...queued, queue_ahead: 0 }, 0), /^Формируется/);
  });

  it('длинная очередь — предупреждение и сводка по правилам', () => {
    const queued = { ...running, status: 'queued' as const };
    assert.match(progressText({ ...queued, queue_ahead: 3 }, 0), /может не успеть — сводка по правилам готова сразу$/);
    assert.doesNotMatch(progressText({ ...queued, queue_ahead: 2 }, 0), /может не успеть/);
    assert.equal(isLongQueue({ ...queued, queue_ahead: 3 }), true);
    assert.equal(isLongQueue({ ...queued, queue_ahead: 2 }), false);
    assert.equal(isLongQueue({ ...queued, status: 'done', queue_ahead: 5 }), false);
    assert.equal(isLongQueue(null), false);
  });
});

describe('sourceCaption', () => {
  it('GigaChat — модель в метке и «сверяйте цифры»', () => {
    const caption = sourceCaption({ source: 'gigachat', model: 'GigaChat-2-Pro' });
    assert.equal(caption.label, 'GigaChat · GigaChat-2-Pro');
    assert.equal(caption.note, 'Сформировано GigaChat — сверяйте цифры с отчётом');
    assert.equal(caption.isAi, true);
    assert.equal(sourceCaption({ source: 'gigachat', model: null }).label, 'GigaChat');
  });

  it('по правилам и тестовый провайдер — без пометки ИИ', () => {
    const rules = sourceCaption({ source: 'rules', model: null });
    assert.equal(rules.label, 'По правилам');
    assert.equal(rules.note, 'Сводка по правилам: шаблон из цифр отчёта, без ИИ');
    assert.equal(rules.isAi, false);
    const stub = sourceCaption({ source: 'stub', model: 'stub' });
    assert.equal(stub.isAi, false);
    assert.match(stub.note, /stub/);
  });
});

describe('checksCaption', () => {
  const base = { numbers_total: 18, numbers_matched: 18, sentences_removed: 0, advice_removed: 0 };

  it('ничего не удалено — все числа есть в отчёте, со склонением', () => {
    assert.equal(checksCaption(base), 'Все 18 чисел есть в данных отчёта');
    assert.equal(checksCaption({ ...base, numbers_total: 3, numbers_matched: 3 }), 'Все 3 числа есть в данных отчёта');
    assert.equal(checksCaption({ ...base, numbers_total: 1, numbers_matched: 1 }), 'Число есть в данных отчёта');
    assert.equal(checksCaption({ ...base, numbers_total: 0, numbers_matched: 0 }), 'Чисел в тексте нет — сверять нечего');
    assert.equal(checksCaption(null), null);
  });

  const reasons = (r: Partial<{ numbers: number; advice: number; evaluative: number; other: number }>) => ({
    numbers: 0,
    advice: 0,
    evaluative: 0,
    other: 0,
    ...r,
  });

  it('причины удаления — по разбивке с сервера', () => {
    assert.equal(
      checksCaption({ ...base, numbers_matched: 17, sentences_removed: 1, removed_by_reason: reasons({ numbers: 1 }) }),
      'Убрана 1 фраза: числа нет в отчёте',
    );
    assert.equal(
      checksCaption({ ...base, sentences_removed: 5, removed_by_reason: reasons({ numbers: 5 }) }),
      'Убрано 5 фраз: чисел нет в отчёте',
    );
    assert.equal(
      checksCaption({ ...base, sentences_removed: 2, advice_removed: 2, removed_by_reason: reasons({ advice: 2 }) }),
      'Убраны 2 фразы: советы вместо фактов',
    );
    assert.equal(
      checksCaption({ ...base, sentences_removed: 1, removed_by_reason: reasons({ evaluative: 1 }) }),
      'Убрана 1 фраза: оценка вместо факта',
    );
    // фраза-совет с числом: числа «не совпали», но причина — совет, а не число
    assert.equal(
      checksCaption({
        ...base,
        numbers_matched: 16,
        sentences_removed: 3,
        advice_removed: 1,
        removed_by_reason: reasons({ numbers: 1, advice: 1, other: 1 }),
      }),
      'Убраны 3 фразы: 1 — числа нет в отчёте, 1 — совет вместо факта, 1 — не прошла проверку',
    );
  });

  it('старые сводки без разбивки — нейтрально, советы отдельно', () => {
    assert.equal(checksCaption({ ...base, numbers_matched: 17, sentences_removed: 1 }), 'Убрана 1 фраза: не прошла проверку');
    assert.equal(
      checksCaption({ ...base, sentences_removed: 2, advice_removed: 2 }),
      'Убраны 2 фразы: советы вместо фактов',
    );
    assert.equal(
      checksCaption({ ...base, numbers_matched: 16, sentences_removed: 3, advice_removed: 1 }),
      'Убраны 3 фразы: 2 — не прошли проверку, 1 — совет вместо факта',
    );
  });
});

describe('errorView', () => {
  const codes = [
    ['llm_unavailable', 'GigaChat сейчас недоступен'],
    ['llm_busy', 'GigaChat занят другими сводками — он обрабатывает по одной'],
    ['llm_timeout', 'GigaChat не ответил вовремя'],
    ['llm_not_configured', 'GigaChat не настроен на этом стенде'],
    ['llm_budget_exceeded', 'Дневной лимит GigaChat исчерпан'],
    ['llm_bad_output', 'Ответ GigaChat не прошёл проверку цифр'],
    ['interrupted', 'Формирование прервано'],
    ['rate_limited', 'Лимит новых сводок GigaChat за час исчерпан'],
    ['internal', 'Не удалось сформировать сводку'],
  ] as const;

  for (const [code, title] of codes) {
    it(`${code} → «${title}»`, () => {
      const view = errorView({ code, message: 'сообщение сервера', rules_available: true });
      assert.equal(view.title, title);
      assert.ok(view.text.length > 0);
      assert.equal(view.offerRules, true);
      assert.equal(view.canRetry, code !== 'llm_not_configured');
    });
  }

  it('тексты без советов по процессам', () => {
    for (const [code] of codes) {
      const { title, text } = errorView({ code, message: '', rules_available: true });
      assert.doesNotMatch(`${title} ${text}`, /рекоменд|следует|необходимо/i);
    }
  });

  it('disabled — флаг выключили: ни повтора, ни сводки по правилам', () => {
    const view = errorView({ code: 'disabled', message: '', rules_available: false });
    assert.equal(view.title, 'Сводки ИИ выключены администратором');
    assert.equal(view.canRetry, false);
    assert.equal(view.offerRules, false);
  });

  it('неизвестный код — сообщение сервера; rules_available=false убирает «по правилам»', () => {
    const view = errorView({ code: 'something_new', message: 'Новый сбой', rules_available: false });
    assert.equal(view.title, 'Не удалось сформировать сводку');
    assert.equal(view.text, 'Новый сбой');
    assert.equal(view.canRetry, true);
    assert.equal(view.offerRules, false);
    assert.equal(errorView(null).title, 'Не удалось сформировать сводку');
  });
});

describe('sectionList', () => {
  const sections = job().result!.sections;

  it('порядок и заголовки для команды', () => {
    assert.deepEqual(
      sectionList(sections, 'team').map((section) => [section.key, section.title]),
      [
        ['headline', 'Коротко'],
        ['going_well', 'Что идёт хорошо'],
        ['lagging', 'Отставание и задержки'],
        ['attention', 'Кто требует внимания'],
        ['questions', 'Вопросы для разбора'],
      ],
    );
  });

  it('у КАМа «Где задержки»; пустые секции и пробелы пропускаются', () => {
    const list = sectionList({ ...sections, going_well: [], questions: ['  ', ''] }, 'kam');
    assert.deepEqual(
      list.map((section) => section.title),
      ['Коротко', 'Отставание и задержки', 'Где задержки'],
    );
    assert.deepEqual(sectionList({ ...sections, headline: ' ' }, 'team')[0]?.key, 'going_well');
    assert.deepEqual(sectionList(null, 'team'), []);
  });
});

describe('periodLabel, summaryTitle, mskDateTime', () => {
  it('период в текущем году — без года; другой год и граница года — с годом', () => {
    assert.equal(periodLabel({ from: '2026-09-16', to: '2026-10-16' }, 2026), '16.09–16.10');
    assert.equal(periodLabel({ from: '2025-09-16', to: '2025-10-16' }, 2026), '16.09–16.10.2025');
    assert.equal(periodLabel({ from: '2025-12-16', to: '2026-01-15' }, 2026), '16.12.2025–15.01.2026');
    assert.equal(periodLabel({ from: '2026-10-16', to: '2026-10-16' }, 2026), '16.10');
    assert.equal(periodLabel({ from: null, to: '2026-10-16' }, 2026), '');
    assert.equal(periodLabel(null, 2026), '');
  });

  it('заголовок команды и КАМа', () => {
    assert.equal(summaryTitle(job(), 2026), 'Сводка ИИ · команда · 16.09–16.10');
    assert.equal(summaryTitle({ ...job(), kind: 'kam', kam_name: 'Иванов И. И.' }, 2026), 'Сводка ИИ · Иванов И. И.');
    assert.equal(summaryTitle({ ...job(), kind: 'kam', kam_name: null }, 2026), 'Сводка ИИ · КАМ');
  });

  it('время по МСК', () => {
    assert.equal(mskDateTime('2026-10-16T11:03:08+00:00'), '16.10.2026 14:03 МСК');
    assert.equal(mskDateTime('2026-10-16T22:30:00Z'), '17.10.2026 01:30 МСК');
    assert.equal(mskDateTime('не дата'), '');
    assert.equal(mskDateTime(null), '');
  });
});

describe('toPlainText', () => {
  it('заголовок, период, источник, «сверяйте цифры» и секции с «— »', () => {
    const text = toPlainText(job(), 2026);
    const lines = text.split('\n');
    assert.equal(lines[0], 'Сводка ИИ · команда · 16.09–16.10');
    assert.equal(lines[1], 'Период: 16.09.2026 — 16.10.2026');
    assert.equal(lines[2], 'Источник: GigaChat · GigaChat-2-Pro, 16.10.2026 14:03 МСК');
    assert.equal(lines[3], 'Сформировано GigaChat — сверяйте цифры с отчётом.');
    assert.ok(text.includes('\n\nКоротко\nНа 16.10.2026 в работе 47 заявок, просрочено 9 (19,1%).'));
    assert.ok(text.includes('\n\nЧто идёт хорошо\n— Уложились в норму: 71,4% решённых этапов.'));
    assert.ok(text.includes('\n\nКто требует внимания\n— Иванов И. И.: собственных просрочек 4 из 12 (33,3%).'));
    assert.ok(text.endsWith('— Что задерживает заявки на этапе «Согласование договора»?'));
    assert.doesNotMatch(text, /КАМ-\d/);
  });

  it('по правилам — своя пометка; без результата — пустая строка', () => {
    const rules = job({
      provider: 'rules',
      mode: 'rules',
      result: { ...job().result!, source: 'rules', model: null },
    });
    const text = toPlainText(rules, 2026);
    assert.match(text, /Источник: По правилам, /);
    assert.match(text, /Сводка по правилам: шаблон из цифр отчёта, без ИИ\./);
    assert.equal(toPlainText(job({ status: 'failed', result: null }), 2026), '');
  });

  it('КАМ — «Где задержки» вместо «Кто требует внимания»', () => {
    const kam = job({ kind: 'kam', kam_id: 'k1', kam_name: 'Петров П. П.' });
    const text = toPlainText(kam, 2026);
    assert.ok(text.startsWith('Сводка ИИ · Петров П. П.\n'));
    assert.ok(text.includes('\n\nГде задержки\n— '));
    assert.ok(!text.includes('Кто требует внимания'));
  });
});

describe('paramsKey и isStale', () => {
  const page = {
    from: '2026-09-16',
    to: '2026-10-16',
    university_id: ['u2', 'u1'],
    direction_id: undefined,
  };

  it('перестановка массивов и пустые значения не меняют ключ', () => {
    assert.equal(
      paramsKey(page),
      paramsKey({ ...page, university_id: ['u1', 'u2'], kam_id: [], direction_id: [], workflow_type: null }),
    );
    assert.notEqual(paramsKey(page), paramsKey({ ...page, workflow_type: 'b2b' }));
    assert.notEqual(paramsKey(page), paramsKey({ ...page, to: '2026-10-17' }));
  });

  it('сводка по КАМу не зависит от фильтра КАМов', () => {
    assert.equal(paramsKey({ ...page, kam_id: ['k1'] }, 'kam'), paramsKey(page, 'kam'));
    assert.notEqual(paramsKey({ ...page, kam_id: ['k1'] }, 'team'), paramsKey(page, 'team'));
  });

  it('isStale сравнивает эхо параметров задачи с фильтрами страницы', () => {
    const done = job({ params: { ...TEAM_PARAMS, university_id: ['u1', 'u2'] } });
    assert.equal(isStale(done, page), false);
    assert.equal(isStale(done, { ...page, university_id: ['u2', 'u1'] }), false);
    assert.equal(isStale(done, { ...page, university_id: ['u1'] }), true);
    assert.equal(isStale(done, { ...page, from: '2026-10-01' }), true);
    assert.equal(isStale(null, page), false);
    const kamJob = job({ kind: 'kam', kam_id: 'k1', params: { ...TEAM_PARAMS, university_id: ['u1', 'u2'] } });
    assert.equal(isStale(kamJob, { ...page, kam_id: ['k9'] }), false);
  });
});

describe('requestBody и pageTarget', () => {
  const page = {
    from: '2026-09-16',
    to: '2026-10-16',
    workflow_type: 'b2b' as const,
    kam_id: ['k2', 'k1'],
    university_id: ['u1'],
  };

  it('команда: фильтры как есть (отсортированы), auto, без force', () => {
    assert.deepEqual(requestBody('team', page, null), {
      kind: 'team',
      kam_id: null,
      params: {
        from: '2026-09-16',
        to: '2026-10-16',
        workflow_type: 'b2b',
        kam_id: ['k1', 'k2'],
        university_id: ['u1'],
        direction_id: [],
      },
      mode: 'auto',
      force: false,
    });
  });

  it('КАМ: kam_id задачи, фильтр КАМов пустой; режим и force пробрасываются', () => {
    const body = requestBody('kam', page, 'k7', { mode: 'rules', force: true });
    assert.equal(body.kind, 'kam');
    assert.equal(body.kam_id, 'k7');
    assert.deepEqual(body.params.kam_id, []);
    assert.equal(body.mode, 'rules');
    assert.equal(body.force, true);
    assert.equal(requestBody('team', page, 'k7').kam_id, null);
  });

  it('«только свои» — сводка по себе, иначе — по команде', () => {
    assert.deepEqual(pageTarget(true, 'me'), { kind: 'kam', kamId: 'me' });
    assert.deepEqual(pageTarget(false, 'me'), { kind: 'team', kamId: null });
    assert.deepEqual(pageTarget(true, undefined), { kind: 'kam', kamId: null });
    const own = pageTarget(true, 'me');
    const body = requestBody(own.kind, { from: '2026-10-01', to: '2026-10-16' }, own.kamId);
    assert.equal(body.kind, 'kam');
    assert.equal(body.kam_id, 'me');
    assert.deepEqual(body.params, {
      from: '2026-10-01',
      to: '2026-10-16',
      workflow_type: null,
      kam_id: [],
      university_id: [],
      direction_id: [],
    });
  });
});
