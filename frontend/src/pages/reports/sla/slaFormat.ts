import type {
  SlaActionKind,
  SlaBucketKey,
  SlaClientRef,
  SlaKamRow,
  SlaLastAction,
  SlaNormSource,
  SlaStageRow,
  SlaTrendPoint,
  SlaUniversityRef,
} from '@/shared/api/endpoints/sla';

/**
 * Чистые форматтеры вкладки «Просрочки»: дни сверх нормы, доли с малой выборкой,
 * корзины старения, подписи дельт и тренда, сортировка таблицы КАМов. Без
 * alias-импортов времени выполнения (только `import type`) — модуль гоняется в
 * `node --test` напрямую (frontend/tests/sla-format.test.ts). Тексты — только факты,
 * без оценок и рекомендаций.
 */

export const EMPTY = '—';
/** Неразрывный пробел перед «%» и единицами. */
const NBSP = ' ';
const MINUS = '−';

export const BUCKET_KEYS: readonly SlaBucketKey[] = ['d1_3', 'd4_7', 'd8_14', 'd15_30', 'd31_plus'];

const BUCKET_LABELS: Record<SlaBucketKey, string> = {
  d1_3: '1–3 дн.',
  d4_7: '4–7 дн.',
  d8_14: '8–14 дн.',
  d15_30: '15–30 дн.',
  d31_plus: 'больше 30 дн.',
};

/** Доля в работе меньше этого — серым с пометкой «мало заявок» (как small_sample API). */
export const SMALL_SAMPLE = 3;

export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

/** Число с 1 знаком и запятой; целое — без «,0»: 3.4 → «3,4», 12 → «12». */
export function formatNumber1(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  const abs = Math.abs(rounded);
  const text = Number.isInteger(abs) ? abs.toFixed(0) : abs.toFixed(1).replace('.', ',');
  return rounded < 0 ? `${MINUS}${text}` : text;
}

/** Целое с разрядами: 12 345 → «12 345». */
export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return Math.round(value)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

/** Задержка сверх нормы: «+3,4 дн.»; null — «—». */
export function formatDelay(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return EMPTY;
  const sign = days > 0 ? '+' : '';
  return `${sign}${formatNumber1(days)}${NBSP}дн.`;
}

/** Дни без знака: «14 дн.», «48,3 дн.». */
export function formatDays(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return EMPTY;
  return `${formatNumber1(days)}${NBSP}дн.`;
}

/** Часы: «51 ч», «5,5 ч». */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || !Number.isFinite(hours)) return EMPTY;
  return `${formatNumber1(hours)}${NBSP}ч`;
}

/** Доля: «57 %», «20,2 %»; null — «—». */
export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return `${formatNumber1(value)}${NBSP}%`;
}

export interface SamplePct {
  text: string;
  /** Малая выборка: показать серым. */
  small: boolean;
  /** Подсказка к серой доле. */
  hint: string | null;
}

/**
 * Доля с учётом малой выборки: при знаменателе меньше `min` значение остаётся, но
 * помечается «мало заявок» — у КАМа с 1 заявкой в работе 100 % ничего не говорит.
 */
export function samplePct(
  value: number | null | undefined,
  base: number,
  min: number = SMALL_SAMPLE,
): SamplePct {
  const small = base > 0 && base < min;
  return {
    text: formatPct(value),
    small,
    hint: small ? `мало заявок: ${base} в работе` : null,
  };
}

/** «4 из 7». */
export function ofText(part: number, total: number): string {
  return `${formatInt(part)} из ${formatInt(total)}`;
}

export function bucketLabel(key: SlaBucketKey): string {
  return BUCKET_LABELS[key] ?? key;
}

/** Тон бейджа задержки: до недели — warning, дольше — danger. */
export function bucketTone(key: SlaBucketKey): 'warning' | 'danger' {
  return key === 'd1_3' || key === 'd4_7' ? 'warning' : 'danger';
}

/** Корзина по задержке в днях (для данных без поля bucket): ceil до целых суток. */
export function bucketOf(delayDays: number): SlaBucketKey {
  const days = Math.ceil(delayDays - 1e-9);
  if (days <= 3) return 'd1_3';
  if (days <= 7) return 'd4_7';
  if (days <= 14) return 'd8_14';
  if (days <= 30) return 'd15_30';
  return 'd31_plus';
}

const NORM_SOURCE_LABELS: Record<SlaNormSource, string> = {
  stage: 'задана для этапа',
  workflow: 'общая для процесса',
  global: 'общая настройка',
};

export function normSourceLabel(source: SlaNormSource): string {
  return NORM_SOURCE_LABELS[source] ?? source;
}

/** «норма 14 дн. (общая для процесса)». */
export function normText(normDays: number, source: SlaNormSource): string {
  return `норма ${formatDays(normDays)} (${normSourceLabel(source)})`;
}

export type DeltaUnit = 'count' | 'pct' | 'days' | 'hours';
export type DeltaMode = 'inverted' | 'normal' | 'neutral';

/**
 * Подпись дельты: «+4 к 01.10», «−3,7 п.п. к 15.09–30.09», «без изменений к 01.10»;
 * null — дельты нет (нет базы или сравнение скрыто).
 */
export function deltaCaption(
  delta: number | null | undefined,
  unit: DeltaUnit,
  label: string,
): string | null {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) return null;
  const suffix = label ? ` ${label}` : '';
  if (Math.round(delta * 10) === 0) return `без изменений${suffix}`;
  const sign = delta > 0 ? '+' : '';
  const units: Record<DeltaUnit, string> = {
    count: '',
    pct: `${NBSP}п.п.`,
    days: `${NBSP}дн.`,
    hours: `${NBSP}ч`,
  };
  const number = unit === 'count' ? formatNumber1(Math.round(delta)) : formatNumber1(delta);
  return `${sign}${number}${units[unit]}${suffix}`;
}

/**
 * Цвет дельты: «inverted» — рост это плохо (просрочки), «normal» — рост это хорошо
 * («Уложились в норму»), «neutral» — объёмы (в работе, решено) без оценки.
 */
export function deltaTone(
  delta: number | null | undefined,
  mode: DeltaMode,
): 'good' | 'bad' | 'neutral' {
  if (delta === null || delta === undefined || mode === 'neutral') return 'neutral';
  if (Math.round(delta * 10) === 0) return 'neutral';
  const up = delta > 0;
  return (mode === 'inverted' ? !up : up) ? 'good' : 'bad';
}

export type KamSortKey =
  | 'kam'
  | 'active'
  | 'overdue'
  | 'inherited'
  | 'overdue_no_action'
  | 'delay'
  | 'on_time_pct'
  | 'delta_overdue';

export type SortDir = 'asc' | 'desc';

/** Значение колонки для сортировки; null — «нет значения», всегда в конце. */
function sortValue(row: SlaKamRow, key: KamSortKey): number | string | null {
  switch (key) {
    case 'kam':
      return row.kam;
    case 'active':
      return row.active;
    case 'overdue':
      return row.own_overdue;
    case 'inherited':
      return row.inherited;
    case 'overdue_no_action':
      return row.overdue_no_action;
    case 'delay':
      return row.delay_avg_days;
    case 'on_time_pct':
      return row.on_time_pct;
    case 'delta_overdue':
      return row.delta?.overdue ?? null;
    default:
      return null;
  }
}

/** Порядок по умолчанию: собственные просрочки ↓, доля ↓, сумма задержки ↓, имя. */
function defaultOrder(a: SlaKamRow, b: SlaKamRow): number {
  if (a.own_overdue !== b.own_overdue) return b.own_overdue - a.own_overdue;
  const pa = a.overdue_pct ?? -1;
  const pb = b.overdue_pct ?? -1;
  if (pa !== pb) return pb - pa;
  const ta = a.delay_total_days ?? 0;
  const tb = b.delay_total_days ?? 0;
  if (ta !== tb) return tb - ta;
  return a.kam.localeCompare(b.kam, 'ru');
}

/**
 * Компаратор таблицы КАМов: null — в конце при любом направлении; при равенстве —
 * порядок по умолчанию. «Просрочено» сортируется по собственным просрочкам, затем по доле.
 */
export function compareKamRows(
  a: SlaKamRow,
  b: SlaKamRow,
  key: KamSortKey = 'overdue',
  dir: SortDir = 'desc',
): number {
  const va = sortValue(a, key);
  const vb = sortValue(b, key);
  if (va === null && vb !== null) return 1;
  if (vb === null && va !== null) return -1;
  if (va !== null && vb !== null && va !== vb) {
    const cmp =
      typeof va === 'string' && typeof vb === 'string'
        ? va.localeCompare(vb, 'ru')
        : (va as number) - (vb as number);
    if (cmp !== 0) return dir === 'asc' ? cmp : -cmp;
  }
  return defaultOrder(a, b);
}

const MSK_OFFSET_MS = 3 * 60 * 60 * 1000;

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/** Части даты по МСК; `YYYY-MM-DD` — как есть, момент (ISO с зоной) — со сдвигом +3. */
function mskParts(value: string): { y: number; m: number; d: number; hh: number; mm: number } | null {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split('-').map(Number) as [number, number, number];
    return { y, m, d, hh: 0, mm: 0 };
  }
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) return null;
  const date = new Date(ms + MSK_OFFSET_MS);
  return {
    y: date.getUTCFullYear(),
    m: date.getUTCMonth() + 1,
    d: date.getUTCDate(),
    hh: date.getUTCHours(),
    mm: date.getUTCMinutes(),
  };
}

/** «16.10.2026» по МСК. */
export function mskDay(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const p = mskParts(value);
  return p ? `${pad(p.d)}.${pad(p.m)}.${p.y}` : EMPTY;
}

/** «16.10» по МСК. */
export function mskShortDay(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const p = mskParts(value);
  return p ? `${pad(p.d)}.${pad(p.m)}` : EMPTY;
}

/** «16.10.2026 14:05» по МСК. */
export function mskDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const p = mskParts(value);
  return p ? `${pad(p.d)}.${pad(p.m)}.${p.y} ${pad(p.hh)}:${pad(p.mm)}` : EMPTY;
}

/** Дата среза точки тренда: `snapshot_at − 1 с` по МСК («23.08»). */
function snapshotDay(snapshotAt: string): string {
  const ms = Date.parse(snapshotAt);
  if (Number.isNaN(ms)) return EMPTY;
  return mskShortDay(new Date(ms - 1000).toISOString());
}

/** Подпись оси тренда: дата среза из API, без неё — по snapshot_at. */
export function trendLabel(
  point: Pick<SlaTrendPoint, 'snapshot_at'> & { label?: string | null },
): string {
  return point.label || snapshotDay(point.snapshot_at);
}

/** Заголовок подсказки точки тренда: «на 23.08», у неполной — с пометкой. */
export function trendTooltipTitle(
  point: Pick<SlaTrendPoint, 'snapshot_at' | 'complete'> & { label?: string | null },
  granularity: 'week' | 'month',
): string {
  const label = trendLabel(point);
  const base = label.startsWith('на ') ? label : `на ${label}`;
  if (point.complete) return base;
  return `${base} · ${granularity === 'month' ? 'месяц' : 'неделя'} не закончил${
    granularity === 'month' ? 'ся' : 'ась'
  }`;
}

/** Подпись под трендом: как построены точки. */
export function trendCaption(granularity: 'week' | 'month'): string {
  return granularity === 'month'
    ? 'по месяцам, на конец месяца МСК'
    : 'по неделям, на конец воскресенья МСК, не короче 8 недель';
}

const ACTION_KIND_LABELS: Record<string, string> = {
  transition: 'переход',
  created: 'создание',
  comment: 'комментарий',
  file: 'файл',
};

export function actionKindLabel(kind: SlaActionKind | null | undefined): string {
  if (!kind) return 'действие';
  return ACTION_KIND_LABELS[kind] ?? 'действие';
}

/** «Павлов Роман Евгеньевич» → «Павлов Р. Е.». */
export function shortName(fullName: string | null | undefined): string {
  if (!fullName) return '';
  const [last, ...rest] = fullName.split(/\s+/).filter(Boolean);
  if (!last) return fullName;
  const initials = rest.map((part) => `${part[0]}.`).join(' ');
  return initials ? `${last} ${initials}` : last;
}

/** «сегодня», «вчера», «12 дн. назад» по days_ago из API. */
export function daysAgoText(daysAgo: number): string {
  const whole = Math.floor(daysAgo + 1e-9);
  if (whole <= 0) return 'сегодня';
  if (whole === 1) return 'вчера';
  return `${whole}${NBSP}дн. назад`;
}

/** «12 дн. назад · комментарий · Павлов Р. Е.»; нет действий — «действий не было». */
export function lastActionText(action: SlaLastAction | null | undefined): string {
  if (!action) return 'действий в системе не было';
  const parts = [daysAgoText(action.days_ago), actionKindLabel(action.kind)];
  const actor = shortName(action.actor);
  if (actor) parts.push(actor);
  return parts.join(' · ');
}

/** Нагрузка КАМа относительно медианы команды: значок в колонке «В работе». */
export function loadMark(
  active: number,
  median: number | null | undefined,
): 'above' | 'below' | 'median' | null {
  if (median === null || median === undefined || !Number.isFinite(median)) return null;
  if (active > median) return 'above';
  if (active < median) return 'below';
  return 'median';
}

/** Вуз (короткое название) или B2C-клиент; ничего — «—». */
export function counterpartyName(
  university: SlaUniversityRef | null | undefined,
  client: SlaClientRef | undefined,
): string {
  if (university) return university.short_name || university.name;
  return client || EMPTY;
}

/** Уложились в норму на этапе: поле API, без него — из доли × решённых. */
export function onTimeCount(row: Pick<SlaStageRow, 'decided' | 'on_time_pct'> & { on_time?: number }): number {
  if (typeof row.on_time === 'number') return row.on_time;
  if (row.on_time_pct === null || row.decided === 0) return 0;
  return Math.round((row.on_time_pct * row.decided) / 100);
}

/** Сокращение для узких мест: «Переговоры с вузом о…». */
export function shorten(label: string, max = 26): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}
