import type { EChartsOption } from 'echarts';
import type {
  SlaAgingRow,
  SlaBucketKey,
  SlaKamRow,
  SlaStageRow,
  SlaTrend,
  SlaTrendPoint,
} from '@/shared/api/endpoints/sla';
import { chartAnimation } from '@/shared/config/echartsTheme';
import { STATUS_TRIPLES, SURFACE } from '@/shared/config/tokens';
import {
  BUCKET_KEYS,
  bucketLabel,
  formatDelay,
  formatInt,
  formatPct,
  normText,
  ofText,
  shortName,
  shorten,
  trendLabel,
  trendTooltipTitle,
} from './slaFormat';

/**
 * Опции ECharts вкладки «Просрочки» (тема 'crm', анимации chartAnimation): «Где
 * застревают» — этапы со старением, «Старение» КАМа, «Нагрузка и просрочки», тренд.
 * Оси — от нуля; подпись периода и источника — внутри графика (попадает в PNG).
 */

export const SOURCE_SLA = 'Альма, история переходов заявок и нормы этапов';
export const SOURCE_SLA_LOAD = 'Альма, заявки в работе по КАМам на дату';

const AXIS_LABEL = { color: SURFACE.mutedForeground, fontSize: 12 };
const SPLIT_LINE = { lineStyle: { color: SURFACE.border } };

function hexToRgb(hex: string): [number, number, number] {
  const value = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16)) as [number, number, number];
}

function mixHex(from: string, to: string, t: number): string {
  const a = hexToRgb(from);
  const b = hexToRgb(to);
  return `#${a
    .map((channel, i) => Math.round(channel + ((b[i] ?? channel) - channel) * t))
    .map((channel) => channel.toString(16).padStart(2, '0'))
    .join('')}`;
}

/**
 * Последовательная шкала старения: от светлого warning к глубокому danger. Чистый
 * tint на белом не виден, поэтому первая ступень — смесь tint и core.
 */
export const BUCKET_COLORS: Record<SlaBucketKey, string> = {
  d1_3: mixHex(STATUS_TRIPLES.warning.tint, STATUS_TRIPLES.warning.core, 0.55),
  d4_7: STATUS_TRIPLES.warning.core,
  d8_14: mixHex(STATUS_TRIPLES.warning.core, STATUS_TRIPLES.danger.core, 0.5),
  d15_30: STATUS_TRIPLES.danger.core,
  d31_plus: STATUS_TRIPLES.danger.deep,
};

/** Подпись периода и источника внутри графика; на узком экране — в 2 строки. */
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

function captionBottom(base: number, compact: boolean): number {
  return compact ? base + 14 : base;
}

/** Высота графика по числу строк-категорий. */
export function rowsHeight(rows: number, min = 220): number {
  return Math.max(min, rows * 30 + 96);
}

// --- «Где застревают» ---------------------------------------------------------------------

/**
 * Этапы процесса в порядке схемы: длина столбца — просроченные на этапе, сегменты —
 * корзины старения. Подпись — «6 из 18 · обычно +12 дн.»; норма и источник — в подсказке.
 */
export function stageOverdueOption(
  rows: SlaStageRow[],
  caption: string,
  compact = false,
): EChartsOption {
  const ordered = [...rows].reverse(); // первый этап схемы сверху
  const last = BUCKET_KEYS[BUCKET_KEYS.length - 1];
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const list = params as Array<{ dataIndex: number }>;
        const row = ordered[list[0]?.dataIndex ?? 0];
        if (!row) return '';
        const lines = [
          `<b>${row.name}</b>`,
          `Просрочено: <b>${ofText(row.overdue, row.active)}</b> в работе${
            row.overdue_pct !== null ? ` (${formatPct(row.overdue_pct)})` : ''
          }`,
        ];
        if (row.overdue > 0) {
          lines.push(
            `Сверх нормы: обычно ${formatDelay(row.delay_median_days)}, в среднем ${formatDelay(
              row.delay_avg_days,
            )}`,
          );
          for (const key of BUCKET_KEYS) {
            const count = row.aging[key] ?? 0;
            if (count > 0) lines.push(`· ${bucketLabel(key)}: ${count}`);
          }
        }
        lines.push(normText(row.norm_days, row.norm_source));
        if (row.decided > 0) {
          lines.push(`Уложились в норму: ${formatPct(row.on_time_pct)} из ${row.decided} решённых`);
        }
        return lines.join('<br/>');
      },
    },
    legend: { top: 0, type: 'scroll', textStyle: AXIS_LABEL },
    grid: {
      left: 8,
      right: compact ? 56 : 150,
      top: 32,
      bottom: captionBottom(40, compact),
      containLabel: true,
    },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'Просрочено, заявок',
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'category',
      data: ordered.map((row) => shorten(row.name, compact ? 14 : 24)),
      axisLabel: AXIS_LABEL,
    },
    series: BUCKET_KEYS.map((key) => ({
      name: bucketLabel(key),
      type: 'bar' as const,
      stack: 'overdue',
      barMaxWidth: 20,
      itemStyle: { color: BUCKET_COLORS[key], borderColor: SURFACE.card, borderWidth: 1 },
      label:
        key === last
          ? {
              show: true,
              position: 'right' as const,
              color: SURFACE.mutedForeground,
              fontSize: 11,
              formatter: (params: unknown) => {
                const row = ordered[(params as { dataIndex: number }).dataIndex];
                if (!row) return '';
                const base = ofText(row.overdue, row.active);
                if (compact || row.overdue === 0 || row.delay_median_days === null) return base;
                return `${base} · обычно ${formatDelay(row.delay_median_days)}`;
              },
            }
          : undefined,
      data: ordered.map((row) => ({ value: row.aging[key] ?? 0, key: row.id })),
    })),
    graphic: captionGraphic(caption, compact),
  };
}

// --- «Старение» КАМа -----------------------------------------------------------------------

/** Просроченные КАМа по корзинам задержки: сейчас и на начало периода (контур). */
export function agingOption(
  rows: SlaAgingRow[],
  caption: string,
  labels: { current: string; previous: string },
  compact = false,
): EChartsOption {
  const hasPrev = rows.some((row) => row.prev !== null);
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const row = rows[(params as Array<{ dataIndex: number }>)[0]?.dataIndex ?? 0];
        if (!row) return '';
        const prev = row.prev !== null ? `<br/>${labels.previous}: ${row.prev}` : '';
        return `<b>Сверх нормы ${row.label}</b><br/>${labels.current}: <b>${row.count}</b>${prev}`;
      },
    },
    legend: hasPrev ? { top: 0, textStyle: AXIS_LABEL } : undefined,
    grid: {
      left: 8,
      right: 16,
      top: hasPrev ? 32 : 16,
      bottom: captionBottom(40, compact),
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row.label),
      axisLabel: { ...AXIS_LABEL, interval: 0, fontSize: compact ? 10 : 12 },
      name: 'Сверх нормы',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: AXIS_LABEL,
    },
    yAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'Заявок',
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    series: [
      ...(hasPrev
        ? [
            {
              name: labels.previous,
              type: 'bar' as const,
              barMaxWidth: 14,
              itemStyle: {
                color: STATUS_TRIPLES.draft.tint,
                borderColor: SURFACE.mutedForeground,
                borderWidth: 1,
              },
              data: rows.map((row) => ({ value: row.prev ?? 0, key: row.key })),
            },
          ]
        : []),
      {
        name: labels.current,
        type: 'bar' as const,
        barMaxWidth: 28,
        label: { show: true, position: 'top' as const, color: SURFACE.mutedForeground, fontSize: 12 },
        data: rows.map((row) => ({
          value: row.count,
          key: row.key,
          itemStyle: { color: BUCKET_COLORS[row.key], borderRadius: [4, 4, 0, 0] },
        })),
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

// --- «Нагрузка и просрочки» ----------------------------------------------------------------

interface LoadGroup {
  x: number;
  y: number;
  rows: SlaKamRow[];
}

/** КАМы с одинаковой точкой (в работе, просрочено) — одним маркером, имена в подсказке. */
export function groupLoadPoints(kams: SlaKamRow[]): LoadGroup[] {
  const groups = new Map<string, LoadGroup>();
  for (const row of kams) {
    const id = `${row.active}:${row.overdue}`;
    const group = groups.get(id);
    if (group) group.rows.push(row);
    else groups.set(id, { x: row.active, y: row.overdue, rows: [row] });
  }
  return [...groups.values()];
}

/**
 * Точка — КАМ: X — в работе, Y — просрочено (шт.); пунктир — медианы команды. Имена
 * подписаны только у КАМов из списка «Просрочек больше, чем по команде».
 */
export function loadScatterOption(
  kams: SlaKamRow[],
  medians: { active: number | null; overdue: number | null },
  caption: string,
  compact = false,
): EChartsOption {
  const groups = groupLoadPoints(kams);
  const markLines: Array<Record<string, unknown>> = [];
  if (medians.active !== null) {
    markLines.push({ xAxis: medians.active, label: { formatter: `медиана ${medians.active}` } });
  }
  if (medians.overdue !== null) {
    markLines.push({ yAxis: medians.overdue, label: { formatter: `медиана ${medians.overdue}` } });
  }
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const group = groups[(params as { dataIndex: number }).dataIndex];
        if (!group) return '';
        return group.rows
          .map(
            (row) =>
              `<b>${row.kam}</b><br/>В работе: ${row.active} · просрочено: ${row.overdue}` +
              (row.inherited > 0 ? ` (при передаче: ${row.inherited})` : '') +
              `<br/>Поступило за период: ${row.inflow}`,
          )
          .join('<br/><br/>');
      },
    },
    grid: { left: 8, right: 32, top: 24, bottom: captionBottom(44, compact), containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'В работе, заявок',
      nameLocation: 'middle',
      nameGap: 26,
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      name: 'Просрочено, заявок',
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    series: [
      {
        type: 'scatter',
        data: groups.map((group) => {
          const attention = group.rows.some((row) => row.attention);
          const allInactive = group.rows.every((row) => !row.is_active);
          const named = group.rows.filter((row) => row.attention).map((row) => shortName(row.kam));
          const color = attention
            ? STATUS_TRIPLES.danger.core
            : allInactive
              ? STATUS_TRIPLES.draft.core
              : STATUS_TRIPLES.progress.core;
          return {
            value: [group.x, group.y],
            key: group.rows.length === 1 ? group.rows[0]?.kam_id : undefined,
            symbolSize: Math.min(26, 10 + (group.rows.length - 1) * 4),
            itemStyle: { color, opacity: 0.85, borderColor: SURFACE.card, borderWidth: 1 },
            label: {
              show: named.length > 0,
              position: 'right' as const,
              color: STATUS_TRIPLES.danger.deep,
              fontSize: compact ? 10 : 11,
              formatter:
                named.length > 1 ? `${named[0]} +${named.length - 1}` : (named[0] ?? ''),
            },
          };
        }),
        markLine:
          markLines.length > 0
            ? {
                silent: true,
                symbol: 'none',
                lineStyle: { color: SURFACE.mutedForeground, type: 'dashed', width: 1 },
                label: { color: SURFACE.mutedForeground, fontSize: 11 },
                data: markLines,
              }
            : undefined,
      },
    ],
    graphic: captionGraphic(caption, compact),
  };
}

// --- Тренд -----------------------------------------------------------------------------------

export type SlaTrendMetric = 'overdue' | 'over30' | 'on_time';

export const TREND_METRICS: Array<{ value: SlaTrendMetric; label: string }> = [
  { value: 'overdue', label: 'Просрочено' },
  { value: 'over30', label: 'Больше 30 дн.' },
  { value: 'on_time', label: 'Уложились в норму, %' },
];

export function isTrendMetric(value: string | null): value is SlaTrendMetric {
  return value === 'overdue' || value === 'over30' || value === 'on_time';
}

function metricValue(point: SlaTrendPoint, metric: SlaTrendMetric): number | null {
  if (metric === 'overdue') return point.overdue;
  if (metric === 'over30') return point.overdue_30_plus;
  return point.on_time_pct;
}

function metricColor(metric: SlaTrendMetric): string {
  if (metric === 'on_time') return STATUS_TRIPLES.success.core;
  if (metric === 'over30') return STATUS_TRIPLES.danger.core;
  return STATUS_TRIPLES.warning.core;
}

function tooltipLines(point: SlaTrendPoint, metric: SlaTrendMetric, minN: number): string[] {
  if (metric === 'on_time') {
    if (point.decided === 0) return ['Решённых этапов нет'];
    const pct =
      point.on_time_pct !== null
        ? ` (${formatPct(point.on_time_pct)})`
        : ` (решено меньше ${minN} — доля не считается)`;
    return [`Уложились в норму: <b>${ofText(point.on_time, point.decided)}</b>${pct}`];
  }
  if (metric === 'over30') {
    return [
      `Больше 30 дн. сверх нормы: <b>${formatInt(point.overdue_30_plus)}</b>`,
      `Просрочено всего: ${formatInt(point.overdue)}`,
    ];
  }
  const pct =
    point.overdue_pct !== null
      ? ` (${formatPct(point.overdue_pct)})`
      : point.active > 0
        ? ` (в работе меньше ${minN} — доля не считается)`
        : '';
  return [`Просрочено: <b>${ofText(point.overdue, point.active)}</b> в работе${pct}`];
}

/**
 * Тренд по срезам: одна ось от нуля; в режиме «Просрочено» — светлая область «в работе».
 * Период отчёта выделен markArea, незаконченная последняя точка — пунктиром.
 */
export function trendOption(
  trend: SlaTrend,
  metric: SlaTrendMetric,
  caption: string,
  compact = false,
): EChartsOption {
  const points = trend.points;
  const labels = points.map((point) => trendLabel(point));
  const values = points.map((point) => metricValue(point, metric));
  const lastIndex = points.length - 1;
  const openTail = lastIndex > 0 && points[lastIndex]?.complete === false;
  const solid = values.map((value, index) => (openTail && index === lastIndex ? null : value));
  const dashed = openTail
    ? values.map((value, index) => (index >= lastIndex - 1 ? value : null))
    : [];
  const periodIdx = points
    .map((point, index) => (point.in_period ? index : -1))
    .filter((index) => index >= 0);
  const periodArea =
    periodIdx.length > 0 && periodIdx.length < points.length
      ? {
          silent: true,
          itemStyle: { color: 'rgba(31, 105, 255, 0.06)' },
          label: {
            show: !compact,
            position: 'insideTop' as const,
            color: SURFACE.mutedForeground,
            fontSize: 11,
            formatter: 'период отчёта',
          },
          data: [
            [
              { xAxis: labels[periodIdx[0] ?? 0] ?? '' },
              { xAxis: labels[periodIdx[periodIdx.length - 1] ?? 0] ?? '' },
            ] as [{ xAxis: string }, { xAxis: string }],
          ],
        }
      : undefined;
  const color = metricColor(metric);
  const name = TREND_METRICS.find((item) => item.value === metric)?.label ?? '';
  const lineBase = {
    type: 'line' as const,
    showSymbol: true,
    symbolSize: 6,
    connectNulls: false,
    itemStyle: { color },
  };
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const list = params as Array<{ dataIndex: number }>;
        const point = points[list[0]?.dataIndex ?? 0];
        if (!point) return '';
        return [
          `<b>${trendTooltipTitle(point, trend.granularity)}</b>`,
          ...tooltipLines(point, metric, trend.min_n),
        ].join('<br/>');
      },
    },
    legend: metric === 'overdue' ? { top: 0, textStyle: AXIS_LABEL, data: ['В работе', name] } : undefined,
    grid: {
      left: 8,
      right: 16,
      top: metric === 'overdue' ? 36 : 20,
      bottom: captionBottom(30, compact),
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: labels,
      axisLabel: { ...AXIS_LABEL, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: metric === 'on_time' ? 100 : undefined,
      minInterval: metric === 'on_time' ? undefined : 1,
      name: metric === 'on_time' ? '%' : 'Заявок',
      nameTextStyle: AXIS_LABEL,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
    },
    series: [
      ...(metric === 'overdue'
        ? [
            {
              name: 'В работе',
              type: 'line' as const,
              showSymbol: false,
              lineStyle: { color: STATUS_TRIPLES.progress.core, width: 1, opacity: 0.35 },
              itemStyle: { color: STATUS_TRIPLES.progress.core },
              areaStyle: { color: STATUS_TRIPLES.progress.tint, opacity: 0.8 },
              data: points.map((point) => point.active),
              z: 1,
            },
          ]
        : []),
      {
        ...lineBase,
        name,
        lineStyle: { color, width: 2 },
        data: solid,
        markArea: periodArea,
        z: 3,
      },
      ...(openTail
        ? [
            {
              ...lineBase,
              name,
              lineStyle: { color, width: 2, type: 'dashed' as const },
              data: dashed,
              z: 2,
            },
          ]
        : []),
    ],
    graphic: captionGraphic(caption, compact),
  };
}
