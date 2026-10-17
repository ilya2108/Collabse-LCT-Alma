import type { EChartsOption } from 'echarts';
import type {
  InteractionsColumnKey,
  InteractionsParams,
  KamActivityDynamicsSeries,
  KamActivityInteractions,
  KamActivityParams,
  KamActivityTalent,
  KamBreakdownRow,
  StageConversionRow,
  UniversitiesStageRow,
} from '@/shared/api/endpoints/kamActivity';
import type { WorkflowType } from '@/shared/api/types';
import { chartAnimation } from '@/shared/config/echartsTheme';
import { CHART_PALETTE, STATUS_COLORS, STATUS_TRIPLES, SURFACE } from '@/shared/config/tokens';
import { dayjs, type Dayjs } from '@/shared/lib/dayjs';

/**
 * Чистые функции страницы «Активность КАМов» (ux.md §6.4): пресеты периода,
 * URL-состояние ↔ параметры API, форматтеры и опции ECharts. Оси графиков — от нуля,
 * в каждом графике подпись периода и источника (попадает и в PNG).
 */

export type PeriodPreset = 'week' | 'month' | 'quarter' | 'prev_quarter' | 'custom';
export type KamActivityTab = 'detail' | 'summary' | 'interactions' | 'sla';

/** Период по умолчанию — «Месяц» (с 1-го числа по сегодня): один для всех вкладок. */
export const DEFAULT_PRESET: PeriodPreset = 'month';
const TABS = new Set<string>(['detail', 'summary', 'interactions', 'sla']);

export const PRESETS: Array<{ value: PeriodPreset; label: string }> = [
  { value: 'week', label: 'Неделя' },
  { value: 'month', label: 'Месяц' },
  { value: 'quarter', label: 'Квартал' },
  { value: 'prev_quarter', label: 'Прошлый квартал' },
  { value: 'custom', label: 'Период…' },
];

const DATE_FMT = 'YYYY-MM-DD';
const PRESET_VALUES = new Set<string>(PRESETS.map((p) => p.value));

export interface KamActivityFilters {
  preset: PeriodPreset;
  from: string | null;
  to: string | null;
  wf: WorkflowType | null;
  kam: string[];
  university: string[];
  /** ИТ-направления — общий фильтр всех вкладок отчёта. */
  direction: string[];
  /** Вкладка «Взаимодействия за период»: ИТ-продукты, программы, статусы (этапы). */
  product: string[];
  program: string[];
  status: string[];
  /** Выбранные колонки таблицы и выгрузки; null — колонки по умолчанию. */
  columns: string[] | null;
  tab: KamActivityTab;
  request: string | null;
}

/**
 * Период пресета (D9): все — по сегодня, «Прошлый квартал» — полный.
 * Квартал считается вручную, без плагина dayjs: startMonth = floor(m / 3) * 3.
 */
export function presetRange(
  preset: Exclude<PeriodPreset, 'custom'>,
  today: Dayjs = dayjs(),
): { from: string; to: string } {
  const day = today.startOf('day');
  const to = day.format(DATE_FMT);
  if (preset === 'week') {
    const offset = (day.day() + 6) % 7; // неделя с понедельника
    return { from: day.subtract(offset, 'day').format(DATE_FMT), to };
  }
  if (preset === 'month') {
    return { from: day.startOf('month').format(DATE_FMT), to };
  }
  const quarterStart = day.month(Math.floor(day.month() / 3) * 3).startOf('month');
  if (preset === 'quarter') {
    return { from: quarterStart.format(DATE_FMT), to };
  }
  const prevStart = quarterStart.subtract(3, 'month');
  return {
    from: prevStart.format(DATE_FMT),
    to: quarterStart.subtract(1, 'day').format(DATE_FMT),
  };
}

export function filtersFromParams(
  params: URLSearchParams,
  defaults: { tab: KamActivityTab },
): KamActivityFilters {
  const presetRaw = params.get('preset');
  const from = params.get('from');
  const to = params.get('to');
  // явный период без пресета — «Произвольный» (ссылка с дашборда, пересланный URL)
  const preset: PeriodPreset =
    presetRaw && PRESET_VALUES.has(presetRaw)
      ? (presetRaw as PeriodPreset)
      : from || to
        ? 'custom'
        : DEFAULT_PRESET;
  const wf = params.get('wf');
  const tab = params.get('tab');
  const columns = params.get('cols');
  return {
    preset,
    from,
    to,
    wf: wf === 'b2b' || wf === 'b2c' ? wf : null,
    kam: params.getAll('kam'),
    university: params.getAll('university'),
    direction: params.getAll('direction'),
    product: params.getAll('product'),
    program: params.getAll('program'),
    status: params.getAll('status'),
    columns: columns ? columns.split(',').filter(Boolean) : null,
    tab: tab && TABS.has(tab) ? (tab as KamActivityTab) : defaults.tab,
    request: params.get('request'),
  };
}

/** Итоговый период запроса: пресет пересчитывается от «сегодня», custom — как есть. */
export function resolvePeriod(
  filters: Pick<KamActivityFilters, 'preset' | 'from' | 'to'>,
  today: Dayjs = dayjs(),
): { from?: string; to?: string } {
  if (filters.preset !== 'custom') return presetRange(filters.preset, today);
  return { from: filters.from ?? undefined, to: filters.to ?? undefined };
}

export function toApiParams(filters: KamActivityFilters, today: Dayjs = dayjs()): KamActivityParams {
  const period = resolvePeriod(filters, today);
  return {
    from: period.from,
    to: period.to,
    workflow_type: filters.wf ?? undefined,
    kam_id: filters.kam.length > 0 ? [...filters.kam].sort() : undefined,
    university_id: filters.university.length > 0 ? [...filters.university].sort() : undefined,
    direction_id: filters.direction.length > 0 ? [...filters.direction].sort() : undefined,
  };
}

export function hasNarrowingFilters(filters: KamActivityFilters): boolean {
  return (
    filters.wf !== null ||
    filters.kam.length > 0 ||
    filters.university.length > 0 ||
    filters.direction.length > 0
  );
}

/** Параметры «Взаимодействий за период»: фильтры отчёта КАМов + продукт, программа, статус. */
export function toInteractionsParams(
  filters: KamActivityFilters,
  today: Dayjs = dayjs(),
): InteractionsParams {
  const sorted = (values: string[]): string[] | undefined =>
    values.length > 0 ? [...values].sort() : undefined;
  return {
    ...toApiParams(filters, today),
    product_id: sorted(filters.product),
    program_id: sorted(filters.program),
    stage_id: sorted(filters.status),
  };
}

export function hasInteractionFilters(filters: KamActivityFilters): boolean {
  return (
    hasNarrowingFilters(filters) ||
    filters.product.length > 0 ||
    filters.program.length > 0 ||
    filters.status.length > 0
  );
}

/** Колонки таблицы: выбранные пользователем (только известные отчёту) или по умолчанию. */
export function resolveColumns(
  selected: string[] | null,
  available: Array<{ key: InteractionsColumnKey }>,
  defaults: InteractionsColumnKey[],
): InteractionsColumnKey[] {
  if (!selected) return defaults;
  const known = new Set(available.map((column) => column.key));
  const picked = available
    .map((column) => column.key)
    .filter((key) => selected.includes(key) && known.has(key));
  return picked.length > 0 ? picked : defaults;
}

// --- форматтеры -----------------------------------------------------------------------

export function plural(n: number, forms: [string, string, string]): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

/** Сдвиг МСК: границы периода и даты отчёта — по московскому времени (D7). */
const MSK_OFFSET_MINUTES = 180;

/** Дата `YYYY-MM-DD` — как есть; момент времени (ISO с зоной) — по МСК. */
export function toMsk(value: string): Dayjs {
  return value.length <= 10 ? dayjs(value) : dayjs(value).utcOffset(MSK_OFFSET_MINUTES);
}

export function formatDay(value: string | null | undefined): string {
  return value ? toMsk(value).format('DD.MM.YYYY') : '—';
}

export function formatShortDay(value: string | null | undefined): string {
  return value ? toMsk(value).format('DD.MM') : '—';
}

/** «Данные на»: для прошедшего периода — его конец, для текущего — момент по МСК. */
export function asOfText(period: { to: string; as_of: string }): string {
  const end = dayjs(`${period.to}T00:00:00+03:00`).add(1, 'day');
  const asOf = dayjs(period.as_of);
  return asOf.isBefore(end)
    ? `данные на ${asOf.utcOffset(MSK_OFFSET_MINUTES).format('DD.MM.YYYY HH:mm')} МСК`
    : 'данные на конец периода';
}

export function formatDays(days: number): string {
  return `${days} дн.`;
}

/** «сегодня» / «вчера» / «N дн. назад» относительно «данных на» отчёта. */
export function daysAgo(value: string | null | undefined, asOf: string): string {
  if (!value) return 'контакта не было';
  const diff = toMsk(asOf).startOf('day').diff(toMsk(value).startOf('day'), 'day');
  if (diff <= 0) return 'сегодня';
  if (diff === 1) return 'вчера';
  return `${diff} дн. назад`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${Number.isInteger(value) ? value : value.toFixed(1).replace('.', ',')}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value.toLocaleString('ru-RU');
}

export const INTERACTION_PARTS: Array<{
  key: keyof Omit<KamActivityInteractions, 'total' | 'returns'>;
  short: string;
  label: string;
}> = [
  { key: 'transitions', short: 'П', label: 'Переходы по этапам' },
  { key: 'comments', short: 'К', label: 'Комментарии' },
  { key: 'files', short: 'Ф', label: 'Файлы' },
  { key: 'activities', short: 'М', label: 'Мероприятия с вузом' },
];

export function interactionsShort(interactions: KamActivityInteractions): string {
  return INTERACTION_PARTS.map((part) => `${part.short} ${interactions[part.key]}`).join(' · ');
}

/** Подпись периода для графиков и PNG: период, сравнение, источник. */
export function chartCaption(period: { from: string; to: string }, source: string): string {
  return `${formatDay(period.from)} — ${formatDay(period.to)} · Источник: ${source}`;
}

export const SOURCE_HISTORY = 'Альма, история переходов заявок';
export const SOURCE_ACTIONS = 'Альма, действия КАМов в заявках и мероприятия';
export const SOURCE_TALENT = 'Альма, пул талантов (история статусов студентов)';

// --- ECharts -------------------------------------------------------------------------

const AXIS_LABEL = { color: SURFACE.mutedForeground, fontSize: 12 };
const SPLIT_LINE = { lineStyle: { color: SURFACE.border } };

/** Подпись периода и источника внутри графика (попадает в PNG); на узком экране — в 2 строки. */
function captionGraphic(caption: string, compact = false): EChartsOption['graphic'] {
  return [
    {
      type: 'text',
      left: 8,
      bottom: 4,
      silent: true,
      style: {
        text: compact ? caption.replace(' · Источник: ', '\nИсточник: ') : caption,
        fill: SURFACE.mutedForeground,
        fontSize: 11,
        lineHeight: 14,
      },
    },
  ];
}

/** Нижнее поле под подпись: на узком экране она в две строки. */
function captionBottom(base: number, compact: boolean): number {
  return compact ? base + 14 : base;
}

function shorten(label: string, max = 26): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

/** «Петров Константин Андреевич» → «Петров К. А.» для осей. */
export function shortPersonName(fullName: string): string {
  const [last, ...rest] = fullName.split(/\s+/).filter(Boolean);
  if (!last) return fullName;
  const initials = rest.map((part) => `${part[0]}.`).join(' ');
  return initials ? `${last} ${initials}` : last;
}

/** Конверсия по этапам: горизонтальные бары «дошли до этапа» в цветах этапов. */
export function stageConversionOption(
  rows: StageConversionRow[],
  caption: string,
  compact = false,
): EChartsOption {
  const ordered = [...rows].reverse(); // первая строка сверху
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const list = params as Array<{ dataIndex: number }>;
        const row = ordered[list[0]?.dataIndex ?? 0];
        if (!row) return '';
        const parts = [
          `<b>${row.name}</b>`,
          `Дошли: <b>${row.reached}</b> (${formatPercent(row.share_of_first_pct)} от входа)`,
        ];
        if (row.step_conversion_pct !== null) {
          parts.push(`Конверсия шага: ${formatPercent(row.step_conversion_pct)}`);
        }
        parts.push(`Сейчас на этапе: ${row.on_stage_at_end}`);
        return parts.join('<br/>');
      },
    },
    grid: { left: 8, right: compact ? 40 : 120, top: 8, bottom: captionBottom(40, compact), containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'Заявок',
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'category',
      data: ordered.map((row) => shorten(row.name, compact ? 14 : 26)),
      axisLabel: AXIS_LABEL,
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 22,
        label: {
          show: true,
          position: 'right',
          color: SURFACE.mutedForeground,
          formatter: (params: unknown) => {
            const row = ordered[(params as { dataIndex: number }).dataIndex];
            if (!row) return '';
            if (compact) return `${row.reached}`;
            const step =
              row.step_conversion_pct !== null ? ` · шаг ${formatPercent(row.step_conversion_pct)}` : '';
            return `${row.reached} · ${formatPercent(row.share_of_first_pct)}${step}`;
          },
        },
        data: ordered.map((row, index) => ({
          value: row.reached,
          key: row.code,
          itemStyle: {
            color: row.color || CHART_PALETTE[index % CHART_PALETTE.length],
            borderRadius: [0, 4, 4, 0],
          },
        })),
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

/** «Где застревают»: средние дни на нефинальных этапах; выше порога — цвет warning. */
export function stageDwellOption(
  rows: StageConversionRow[],
  caption: string,
  compact = false,
): EChartsOption {
  const active = rows.filter((row) => !row.is_terminal).reverse();
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const list = params as Array<{ dataIndex: number }>;
        const row = active[list[0]?.dataIndex ?? 0];
        if (!row) return '';
        return [
          `<b>${row.name}</b>`,
          `В среднем: <b>${row.avg_days ?? '—'}</b> дн. (порог ${row.threshold_days ?? '—'})`,
          `Зависли сейчас: ${row.stuck}`,
          `Интервалов в расчёте: ${row.intervals}`,
        ].join('<br/>');
      },
    },
    grid: { left: 8, right: compact ? 40 : 96, top: 8, bottom: captionBottom(40, compact), containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      name: 'Дней на этапе (среднее)',
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'category',
      data: active.map((row) => shorten(row.name, compact ? 14 : 26)),
      axisLabel: AXIS_LABEL,
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 22,
        label: {
          show: true,
          position: 'right',
          color: SURFACE.mutedForeground,
          formatter: (params: unknown) => {
            const row = active[(params as { dataIndex: number }).dataIndex];
            if (!row || row.avg_days === null) return '';
            if (compact) return `${row.avg_days}`;
            return row.stuck > 0 ? `${row.avg_days} · зависли ${row.stuck}` : `${row.avg_days}`;
          },
        },
        data: active.map((row) => ({
          value: row.avg_days ?? 0,
          key: row.code,
          itemStyle: {
            color:
              row.avg_days !== null && row.threshold_days !== null && row.avg_days > row.threshold_days
                ? STATUS_TRIPLES.warning.core
                : STATUS_COLORS.inProgress,
            borderRadius: [0, 4, 4, 0],
          },
        })),
        markLine:
          active.length > 0 && active.every((row) => row.threshold_days === active[0]?.threshold_days)
            ? {
                silent: true,
                symbol: 'none',
                lineStyle: { color: STATUS_TRIPLES.warning.deep, type: 'dashed' },
                label: { formatter: 'порог', color: STATUS_TRIPLES.warning.deep },
                data: [{ xAxis: active[0]?.threshold_days ?? 0 }],
              }
            : undefined,
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

const DYNAMICS_COLORS: Record<string, string> = {
  transitions: CHART_PALETTE[0],
  comments: CHART_PALETTE[2],
  files: CHART_PALETTE[3],
  activities: CHART_PALETTE[1],
};

/** Динамика взаимодействий: составные бары П/К/Ф/М по бакетам + линия «Новые заявки». */
export function interactionsDynamicsOption(
  series: KamActivityDynamicsSeries[],
  granularity: 'day' | 'week' | 'month',
  caption: string,
  compact = false,
): EChartsOption {
  const dates = series[0]?.points.map((point) => point.date) ?? [];
  const label = (date: string): string =>
    granularity === 'month'
      ? dayjs(date).format('MMM YYYY')
      : granularity === 'week'
        ? `с ${dayjs(date).format('DD.MM')}`
        : dayjs(date).format('DD.MM');
  const bars = series.filter((item) => item.key !== 'new_requests');
  const created = series.find((item) => item.key === 'new_requests');
  return {
    ...chartAnimation(),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, type: 'scroll', textStyle: AXIS_LABEL },
    grid: { left: 8, right: 16, top: 36, bottom: captionBottom(30, compact), containLabel: true },
    xAxis: { type: 'category', data: dates.map(label), axisLabel: AXIS_LABEL },
    yAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'Действий',
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    series: [
      ...bars.map((item) => ({
        name: item.label,
        type: 'bar' as const,
        stack: 'interactions',
        barMaxWidth: 28,
        itemStyle: {
          color: DYNAMICS_COLORS[item.key] ?? CHART_PALETTE[4],
          borderWidth: 1,
          borderColor: SURFACE.card,
        },
        data: item.points.map((point) => point.value),
      })),
      ...(created
        ? [
            {
              name: created.label,
              type: 'line' as const,
              smooth: true,
              symbolSize: 6,
              itemStyle: { color: STATUS_COLORS.success },
              lineStyle: { color: STATUS_COLORS.success, width: 2 },
              data: created.points.map((point) => point.value),
            },
          ]
        : []),
    ],
    graphic: captionGraphic(caption, compact),
  };
}

/** Взаимодействия по КАМам: текущий и прошлый период (сгруппированные бары). */
export function kamCompareOption(
  rows: KamBreakdownRow[],
  caption: string,
  labels: { current: string; previous: string },
  compact = false,
): EChartsOption {
  const ordered = [...rows].reverse();
  return {
    ...chartAnimation(),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, textStyle: AXIS_LABEL },
    grid: { left: 8, right: 32, top: compact ? 48 : 32, bottom: captionBottom(30, compact), containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'category',
      data: ordered.map((row) => shortPersonName(row.kam)),
      axisLabel: AXIS_LABEL,
    },
    series: [
      {
        name: labels.previous,
        type: 'bar',
        barMaxWidth: 12,
        itemStyle: { color: STATUS_TRIPLES.draft.tint, borderColor: SURFACE.mutedForeground, borderWidth: 1 },
        data: ordered.map((row) => ({ value: row.interactions_prev, key: row.kam_id })),
      },
      {
        name: labels.current,
        type: 'bar',
        barMaxWidth: 12,
        itemStyle: { color: STATUS_COLORS.inProgress, borderRadius: [0, 3, 3, 0] },
        label: { show: true, position: 'right', color: SURFACE.mutedForeground, fontSize: 11 },
        data: ordered.map((row) => ({ value: row.interactions, key: row.kam_id })),
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

/** Студенты на программах: статусы воронки на конец периода (фиолетовая гамма). */
export function talentStatusOption(
  talent: KamActivityTalent,
  caption: string,
  compact = false,
): EChartsOption {
  const ordered = [...talent.by_status].reverse();
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const row = ordered[(params as Array<{ dataIndex: number }>)[0]?.dataIndex ?? 0];
        return row ? `<b>${row.label}</b>: ${row.value}<br/>За период: +${row.entered}` : '';
      },
    },
    grid: { left: 8, right: compact ? 48 : 96, top: 8, bottom: captionBottom(30, compact), containLabel: true },
    xAxis: { type: 'value', min: 0, minInterval: 1, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    yAxis: { type: 'category', data: ordered.map((row) => row.label), axisLabel: AXIS_LABEL },
    series: [
      {
        type: 'bar',
        barMaxWidth: 22,
        label: {
          show: true,
          position: 'right',
          color: SURFACE.mutedForeground,
          formatter: (params: unknown) => {
            const row = ordered[(params as { dataIndex: number }).dataIndex];
            if (!row) return '';
            return compact ? `${row.value}` : `${row.value} · +${row.entered}`;
          },
        },
        data: ordered.map((row) => ({
          value: row.value,
          key: row.key,
          itemStyle: { color: row.color, borderRadius: [0, 4, 4, 0] },
        })),
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

/**
 * Главная диаграмма вида Б: вузы B2B по этапам на конец периода (текущий — цвет этапа,
 * прошлый период той же длины — светлый контур рядом). Ось — от нуля.
 */
export function universitiesByStageOption(
  rows: Array<Pick<UniversitiesStageRow, 'code' | 'name' | 'color' | 'value' | 'prev' | 'universities'>>,
  caption: string,
  labels: { current: string; previous: string },
  compact = false,
): EChartsOption {
  const ordered = [...rows].reverse(); // первый этап сверху
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const row = ordered[(params as Array<{ dataIndex: number }>)[0]?.dataIndex ?? 0];
        if (!row) return '';
        const names = row.universities.length > 0 ? `<br/>${row.universities.join(', ')}` : '';
        return `<b>${row.name}</b><br/>Вузов: <b>${row.value}</b> (прошлый период — ${row.prev})${names}`;
      },
    },
    legend: { top: 0, textStyle: AXIS_LABEL },
    grid: { left: 8, right: compact ? 40 : 72, top: compact ? 48 : 32, bottom: captionBottom(40, compact), containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'Вузов',
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'category',
      data: ordered.map((row) => shorten(row.name, compact ? 14 : 26)),
      axisLabel: AXIS_LABEL,
    },
    series: [
      {
        name: labels.previous,
        type: 'bar',
        barMaxWidth: 10,
        itemStyle: { color: STATUS_TRIPLES.draft.tint, borderColor: SURFACE.mutedForeground, borderWidth: 1 },
        data: ordered.map((row) => ({ value: row.prev, key: row.code })),
      },
      {
        name: labels.current,
        type: 'bar',
        barMaxWidth: 16,
        label: { show: true, position: 'right', color: SURFACE.mutedForeground, fontSize: 12 },
        data: ordered.map((row, index) => ({
          value: row.value,
          key: row.code,
          itemStyle: {
            // «Без заявок в работе» — нейтральный серый, этапы — в своих цветах
            color:
              row.code === 'without_requests'
                ? SURFACE.mutedForeground
                : row.color || CHART_PALETTE[index % CHART_PALETTE.length],
            borderRadius: [0, 4, 4, 0],
          },
        })),
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

/** Высота графика с категориями по строкам: не меньше min, по ~28px на строку. */
export function rowsHeight(rows: number, min = 240): number {
  return Math.max(min, rows * 28 + 72);
}
