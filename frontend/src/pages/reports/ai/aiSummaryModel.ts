import type {
  AiSummaryChecks,
  AiSummaryCreate,
  AiSummaryError,
  AiSummaryJob,
  AiSummaryKind,
  AiSummaryMode,
  AiSummaryParams,
  AiSummaryResult,
  AiSummarySections,
  AiSummaryStatus,
} from '@/shared/api/endpoints/aiSummary';
import type { KamActivityParams } from '@/shared/api/endpoints/kamActivity';

/**
 * Чистые функции «Сводки ИИ» (ux.md §6.4): интервалы опроса, подписи прогресса, источника
 * и сверки чисел, тексты сбоев, порядок секций, текст для буфера обмена, сравнение
 * фильтров и тело запроса. Только `import type` — модуль гоняется в `node --test` напрямую
 * (frontend/tests/ai-summary.test.ts), без алиасов времени выполнения. Тексты — факты,
 * без оценок и советов.
 */

/** Задача ещё формируется. */
export function isPending(status: AiSummaryStatus | null | undefined): boolean {
  return status === 'queued' || status === 'running';
}

/** Опрос: 2 с первую минуту, потом 4 с; клиентский предел — 15 минут. */
export const POLL_FAST_MS = 2000;
export const POLL_SLOW_MS = 4000;
export const POLL_FAST_WINDOW_MS = 60_000;
export const POLL_LIMIT_MS = 15 * 60_000;

/** `refetchInterval` для GET задачи: false — опрос не нужен (завершена или предел). */
export function pollInterval(job: AiSummaryJob | null | undefined, elapsedMs: number): number | false {
  if (!job || !isPending(job.status)) return false;
  if (elapsedMs >= POLL_LIMIT_MS) return false;
  const base = job.poll_after_ms && job.poll_after_ms > 0 ? job.poll_after_ms : POLL_FAST_MS;
  return elapsedMs < POLL_FAST_WINDOW_MS ? base : Math.max(base, POLL_SLOW_MS);
}

/** Ждём дольше клиентского предела — опрос остановлен. */
export function isPollExpired(job: AiSummaryJob | null | undefined, elapsedMs: number): boolean {
  return Boolean(job && isPending(job.status) && elapsedMs >= POLL_LIMIT_MS);
}

export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

/** «23 с», «1 мин 5 с», «3 мин». */
export function formatElapsed(elapsedMs: number): string {
  const total = Math.max(0, Math.floor(elapsedMs / 1000));
  if (total < 60) return `${total} с`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return seconds === 0 ? `${minutes} мин` : `${minutes} мин ${seconds} с`;
}

/**
 * Строка состояния, пока сводка формируется: «Формируется · 23 с · обычно до минуты»;
 * в очереди GigaChat — «В очереди: перед вами 2 сводки — GigaChat обрабатывает по одной».
 */
export function progressText(job: AiSummaryJob | null | undefined, elapsedMs: number): string {
  if (!job) return 'Собираем данные отчёта…';
  const elapsed = formatElapsed(elapsedMs);
  const ahead = job.queue_ahead ?? 0;
  if (ahead > 0) {
    const text = `В очереди: перед вами ${ahead} ${plural(ahead, ['сводка', 'сводки', 'сводок'])} — GigaChat обрабатывает по одной · ${elapsed}`;
    return isLongQueue(job) ? `${text} · может не успеть — сводка по правилам готова сразу` : text;
  }
  let hint = 'обычно до минуты';
  if (elapsedMs >= 180_000) hint = 'дольше обычного — можно скрыть, готовая сводка сохранится на час';
  else if (elapsedMs >= 60_000) hint = 'иногда до 2–3 минут';
  return `Формируется · ${elapsed} · ${hint}`;
}

/** Очередь шлюза ждёт слот до 3 минут: с тремя сводками впереди GigaChat может не успеть. */
export const LONG_QUEUE = 3;

export function isLongQueue(job: Pick<AiSummaryJob, 'queue_ahead' | 'status'> | null | undefined): boolean {
  return Boolean(job && isPending(job.status) && (job.queue_ahead ?? 0) >= LONG_QUEUE);
}

export interface SourceCaption {
  /** Короткая метка источника: «GigaChat · GigaChat-2-Pro», «По правилам». */
  label: string;
  /** Плашка-предупреждение под заголовком. */
  note: string;
  /** Текст от модели — плашка заметнее. */
  isAi: boolean;
}

export function sourceCaption(result: Pick<AiSummaryResult, 'source' | 'model'>): SourceCaption {
  if (result.source === 'rules') {
    return {
      label: 'По правилам',
      note: 'Сводка по правилам: шаблон из цифр отчёта, без ИИ',
      isAi: false,
    };
  }
  if (result.source === 'stub') {
    return {
      label: 'Тестовый провайдер',
      note: 'Тестовый ответ без GigaChat (stub) — только для проверки стенда',
      isAi: false,
    };
  }
  return {
    label: result.model ? `GigaChat · ${result.model}` : 'GigaChat',
    note: 'Сформировано GigaChat — сверяйте цифры с отчётом',
    isAi: true,
  };
}

const NUMBER_FORMS: [string, string, string] = ['число', 'числа', 'чисел'];
const PHRASE_FORMS: [string, string, string] = ['фраза', 'фразы', 'фраз'];

/** «Убрана» / «Убраны» / «Убрано» — в лад с числом фраз. */
function removedVerb(n: number): string {
  return plural(n, ['Убрана', 'Убраны', 'Убрано']);
}

/** Подпись причины удаления для n фраз. */
const REMOVED_REASONS: Array<[keyof NonNullable<AiSummaryChecks['removed_by_reason']>, (n: number) => string]> = [
  ['numbers', (n) => (n === 1 ? 'числа нет в отчёте' : 'чисел нет в отчёте')],
  ['advice', (n) => (n === 1 ? 'совет вместо факта' : 'советы вместо фактов')],
  ['evaluative', (n) => (n === 1 ? 'оценка вместо факта' : 'оценки вместо фактов')],
  ['other', (n) => (n === 1 ? 'не прошла проверку' : 'не прошли проверку')],
];

/**
 * Итог проверки: «Все 18 чисел есть в данных отчёта» или «Убрана 1 фраза: числа нет
 * в отчёте». Проверка подтверждает, что число есть в отчёте, но не что метрика названа
 * верно, — поэтому «есть в данных», а не «сверено», и плашка «сверяйте цифры» остаётся.
 */
export function checksCaption(checks: AiSummaryChecks | null | undefined): string | null {
  if (!checks) return null;
  const removed = Math.max(0, checks.sentences_removed);
  if (removed === 0) {
    const total = Math.max(0, checks.numbers_total);
    if (total === 0) return 'Чисел в тексте нет — сверять нечего';
    if (total === 1) return 'Число есть в данных отчёта';
    return `Все ${total} ${plural(total, NUMBER_FORMS)} есть в данных отчёта`;
  }
  const head = `${removedVerb(removed)} ${removed} ${plural(removed, PHRASE_FORMS)}`;
  const byReason = checks.removed_by_reason;
  let parts: Array<[number, string]>;
  if (byReason) {
    parts = REMOVED_REASONS.map(([key, label]): [number, string] => {
      const n = Math.max(0, byReason[key] ?? 0);
      return [n, label(n)];
    }).filter(([n]) => n > 0);
  } else {
    // сводки до разбивки по причинам: известны только советы
    const advice = Math.min(removed, Math.max(0, checks.advice_removed));
    const other = removed - advice;
    parts = [];
    if (other > 0) parts.push([other, other === 1 ? 'не прошла проверку' : 'не прошли проверку']);
    if (advice > 0) parts.push([advice, advice === 1 ? 'совет вместо факта' : 'советы вместо фактов']);
  }
  if (parts.length === 0) return `${head}: не прошла проверку`;
  if (parts.length === 1) return `${head}: ${parts[0][1]}`;
  return `${head}: ${parts.map(([n, label]) => `${n} — ${label}`).join(', ')}`;
}

export interface ErrorView {
  title: string;
  text: string;
  /** Кнопка «Повторить». */
  canRetry: boolean;
  /** Кнопка «Сводка по правилам». */
  offerRules: boolean;
}

const ERROR_VIEWS: Record<string, Pick<ErrorView, 'title' | 'text'>> = {
  llm_unavailable: {
    title: 'GigaChat сейчас недоступен',
    text: 'Повторите позже или сформируйте сводку по правилам — те же цифры отчёта, без ИИ.',
  },
  llm_busy: {
    title: 'GigaChat занят другими сводками — он обрабатывает по одной',
    text: 'Повторите через минуту или сформируйте сводку по правилам.',
  },
  llm_timeout: {
    title: 'GigaChat не ответил вовремя',
    text: 'Повторите или сформируйте сводку по правилам.',
  },
  llm_not_configured: {
    title: 'GigaChat не настроен на этом стенде',
    text: 'Сводка по правилам работает и без него. Ключ GigaChat задаёт администратор стенда.',
  },
  llm_budget_exceeded: {
    title: 'Дневной лимит GigaChat исчерпан',
    text: 'Лимит обновится в полночь по Москве. Сводка по правилам доступна сейчас.',
  },
  llm_bad_output: {
    title: 'Ответ GigaChat не прошёл проверку цифр',
    text: 'В тексте оказались числа, которых нет в отчёте, или советы вместо фактов — такой текст не показываем. Повторите или сформируйте сводку по правилам.',
  },
  interrupted: {
    title: 'Формирование прервано',
    text: 'Сервис перезапускался, пока готовилась сводка. Повторите.',
  },
  rate_limited: {
    title: 'Лимит новых сводок GigaChat за час исчерпан',
    text: 'Лимит действует на пользователя, чтобы один человек не израсходовал дневной лимит GigaChat за всех; готовые сводки из кэша в него не входят. Повторите позже или сформируйте сводку по правилам.',
  },
  disabled: {
    title: 'Сводки ИИ выключены администратором',
    text: 'Задача остановлена до обращения к GigaChat.',
  },
  internal: {
    title: 'Не удалось сформировать сводку',
    text: 'Повторите или сформируйте сводку по правилам.',
  },
};

/** Заголовок, текст и доступные действия для задачи в статусе `failed`. */
export function errorView(error: AiSummaryError | null | undefined): ErrorView {
  const code = error?.code ?? 'internal';
  const known = ERROR_VIEWS[code];
  const base = known ?? {
    title: 'Не удалось сформировать сводку',
    text: error?.message?.trim() || 'Повторите или сформируйте сводку по правилам.',
  };
  return {
    ...base,
    canRetry: code !== 'llm_not_configured' && code !== 'disabled',
    offerRules: error?.rules_available !== false && code !== 'disabled',
  };
}

export type AiSectionKey = keyof AiSummarySections;

export interface AiSectionView {
  key: AiSectionKey;
  title: string;
  items: string[];
}

/** Секции в порядке показа; пустые пропускаются. У КАМа «Кто требует внимания» → «Где задержки». */
export function sectionList(
  sections: AiSummarySections | null | undefined,
  kind: AiSummaryKind,
): AiSectionView[] {
  if (!sections) return [];
  const clean = (items: readonly string[] | null | undefined): string[] =>
    (items ?? []).map((item) => item.trim()).filter((item) => item.length > 0);
  const all: AiSectionView[] = [
    { key: 'headline', title: 'Коротко', items: clean([sections.headline ?? '']) },
    { key: 'going_well', title: 'Что идёт хорошо', items: clean(sections.going_well) },
    { key: 'lagging', title: 'Отставание и задержки', items: clean(sections.lagging) },
    {
      key: 'attention',
      title: kind === 'kam' ? 'Где задержки' : 'Кто требует внимания',
      items: clean(sections.attention),
    },
    { key: 'questions', title: 'Вопросы для разбора', items: clean(sections.questions) },
  ];
  return all.filter((section) => section.items.length > 0);
}

/** «2026-09-16» → [2026, 9, 16]. */
function isoDateParts(value: string | null | undefined): [number, number, number] | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value ?? '');
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

const pad = (n: number): string => String(n).padStart(2, '0');

/**
 * Период сводки: «16.09–16.10» в текущем году, «16.09–16.10.2025» в другом,
 * «16.12.2025–15.01.2026» через границу года, «16.10» — один день.
 */
export function periodLabel(
  params: Pick<AiSummaryParams, 'from' | 'to'> | null | undefined,
  currentYear: number = new Date().getFullYear(),
): string {
  const from = isoDateParts(params?.from);
  const to = isoDateParts(params?.to);
  if (!from || !to) return '';
  const [fy, fm, fd] = from;
  const [ty, tm, td] = to;
  if (fy !== ty) return `${pad(fd)}.${pad(fm)}.${fy}–${pad(td)}.${pad(tm)}.${ty}`;
  const year = ty === currentYear ? '' : `.${ty}`;
  if (fm === tm && fd === td) return `${pad(td)}.${pad(tm)}${year}`;
  return `${pad(fd)}.${pad(fm)}–${pad(td)}.${pad(tm)}${year}`;
}

/** «Сводка ИИ · команда · 16.09–16.10» или «Сводка ИИ · Иванов И. И.». */
export function summaryTitle(
  job: Pick<AiSummaryJob, 'kind' | 'kam_name' | 'params'>,
  currentYear?: number,
): string {
  if (job.kind === 'kam') return `Сводка ИИ · ${job.kam_name?.trim() || 'КАМ'}`;
  const period = periodLabel(job.params, currentYear);
  return period ? `Сводка ИИ · команда · ${period}` : 'Сводка ИИ · команда';
}

const MSK_OFFSET_MS = 3 * 3600_000;

/** «16.10.2026 14:03 МСК»; невалидная дата — пустая строка. */
export function mskDateTime(value: string | null | undefined): string {
  const time = value ? Date.parse(value) : Number.NaN;
  if (Number.isNaN(time)) return '';
  const d = new Date(time + MSK_OFFSET_MS);
  return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} МСК`;
}

function fullPeriod(params: AiSummaryParams | null | undefined): string {
  const from = isoDateParts(params?.from);
  const to = isoDateParts(params?.to);
  if (!from || !to) return '';
  return `${pad(from[2])}.${pad(from[1])}.${from[0]} — ${pad(to[2])}.${pad(to[1])}.${to[0]}`;
}

/**
 * Текст для буфера обмена: заголовок, период, источник с временем, «сверяйте цифры»,
 * секции (заголовок, пункты с «— »). Пустая строка — нечего копировать.
 */
export function toPlainText(job: AiSummaryJob, currentYear?: number): string {
  const result = job.result;
  if (!result) return '';
  const caption = sourceCaption(result);
  const lines: string[] = [summaryTitle(job, currentYear)];
  const period = fullPeriod(job.params);
  if (period) lines.push(`Период: ${period}`);
  const generated = mskDateTime(result.generated_at);
  lines.push(`Источник: ${caption.label}${generated ? `, ${generated}` : ''}`);
  lines.push(`${caption.note}.`);
  for (const section of sectionList(result.sections, job.kind)) {
    lines.push('', section.title);
    if (section.key === 'headline') lines.push(...section.items);
    else lines.push(...section.items.map((item) => `— ${item}`));
  }
  return lines.join('\n');
}

type ParamsLike = KamActivityParams | AiSummaryParams;

function sortedIds(values: readonly string[] | null | undefined): string[] {
  return [...new Set(values ?? [])].filter(Boolean).sort();
}

/**
 * Канонический ключ фильтров: массивы отсортированы, пустые значения — как отсутствие.
 * Для сводки по КАМу фильтр КАМов не участвует — сервер его игнорирует.
 */
export function paramsKey(params: ParamsLike | null | undefined, kind: AiSummaryKind = 'team'): string {
  const key: Record<string, string | string[]> = {};
  if (params?.from) key.from = params.from;
  if (params?.to) key.to = params.to;
  if (params?.workflow_type) key.workflow_type = params.workflow_type;
  const lists: Array<[string, readonly string[] | null | undefined]> = [
    ['kam_id', kind === 'kam' ? [] : params?.kam_id],
    ['university_id', params?.university_id],
    ['direction_id', params?.direction_id],
  ];
  for (const [name, values] of lists) {
    const ids = sortedIds(values);
    if (ids.length > 0) key[name] = ids;
  }
  return JSON.stringify(key);
}

/** Фильтры страницы ушли от параметров, по которым сформирована сводка. */
export function isStale(job: AiSummaryJob | null | undefined, params: ParamsLike): boolean {
  if (!job) return false;
  return paramsKey(job.params, job.kind) !== paramsKey(params, job.kind);
}

export interface AiSummaryTarget {
  kind: AiSummaryKind;
  kamId: string | null;
}

/**
 * Что сводит кнопка общей страницы: команду или — при «только свои» (своей роли
 * выдали право с областью «свои») — самого пользователя.
 */
export function pageTarget(ownOnly: boolean, userId: string | null | undefined): AiSummaryTarget {
  return ownOnly ? { kind: 'kam', kamId: userId ?? null } : { kind: 'team', kamId: null };
}

/** Тело `POST /reports/ai-summaries`. */
export function requestBody(
  kind: AiSummaryKind,
  params: ParamsLike,
  kamId: string | null,
  options: { mode?: AiSummaryMode; force?: boolean } = {},
): AiSummaryCreate {
  return {
    kind,
    kam_id: kind === 'kam' ? kamId : null,
    params: {
      from: params.from ?? null,
      to: params.to ?? null,
      workflow_type: params.workflow_type ?? null,
      kam_id: kind === 'kam' ? [] : sortedIds(params.kam_id),
      university_id: sortedIds(params.university_id),
      direction_id: sortedIds(params.direction_id),
    },
    mode: options.mode ?? 'auto',
    force: options.force ?? false,
  };
}
