import type { EChartsOption } from 'echarts';
import type {
  DashboardStageItem,
  KamWorkloadItem,
  ProgramDemandItem,
} from '@/shared/api/endpoints/reports';
import type { ReportDynamicsSeries, ReportSeriesItem } from '@/shared/api/types';
import { chartAnimation } from '@/shared/config/echartsTheme';
import { CHART_PALETTE, STATUS_COLORS, SURFACE, TALENT_FUNNEL_RAMP } from '@/shared/config/tokens';

/**
 * Опции ECharts для виджетов дашборда (ux.md §6.1). Цвета этапов приходят
 * из конфига workflow внутри отчёта (§7.2) — дашборд и канбан раскрашены из
 * одного источника; фолбэк — категориальная палитра темы 'crm'
 * (redesign.md §2.6, hex из tokens.ts — где нужны строки).
 *
 * Объективность диаграмм (критерий финала): счётные величины — бары от нуля с
 * подписью значения (ширина пропорциональна числу, пустой этап — пустой), линии
 * динамики — без сглаживания и с точками (между неделями данных нет), у осей —
 * единицы. «Отказ» — не хвост последовательности этапов, а отдельная строка.
 */

const FALLBACK_PALETTE = [...CHART_PALETTE];

/** Фиолетовый рамп воронки студентов (§2.6). */
const TALENT_PALETTE = [...TALENT_FUNNEL_RAMP];

const AXIS_LABEL = { color: SURFACE.mutedForeground, fontSize: 12 };
const AXIS_NAME = { color: SURFACE.mutedForeground, fontSize: 11 };
const SPLIT_LINE = { lineStyle: { color: SURFACE.border } };
const VALUE_LABEL = { show: true, position: 'right' as const, color: SURFACE.foreground };

const WORKFLOW_LABELS: Record<string, string> = { b2b: 'B2B', b2c: 'B2C' };

function seriesColor(item: { color?: string | null }, index: number, palette = FALLBACK_PALETTE): string {
  return item.color || palette[index % palette.length]!;
}

/** Имена вузов и КАМов в html-tooltip — пользовательские данные: экранируем. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function truncate(value: string, max = 28): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/** Высота графика с категориями по строкам: не меньше min, по ~28px на строку. */
export function rowsHeight(rows: number, min = 280): number {
  return Math.max(min, rows * 28 + 72);
}

/** Пусто — когда все значения нулевые (а не когда массив пуст: этапы приходят всегда). */
export function allZero(values: Iterable<number>): boolean {
  for (const value of values) if (value !== 0) return false;
  return true;
}

// --- «Заявки по текущему этапу» -------------------------------------------------------

/** Ключ клика по этапу: процесс и код (при «Все» этапы двух процессов на одном графике). */
export function stageClickKey(item: Pick<DashboardStageItem, 'workflow_type' | 'key'>): string {
  return `${item.workflow_type}:${item.key}`;
}

export function parseStageClickKey(dataKey: string): { wf: string; status: string } | null {
  const [wf, status] = dataKey.split(':');
  return wf && status ? { wf, status } : null;
}

interface StageGroup {
  wf: string;
  bars: DashboardStageItem[];
  lost: DashboardStageItem[];
}

function stageGroups(series: DashboardStageItem[]): StageGroup[] {
  const groups = new Map<string, StageGroup>();
  for (const item of series) {
    const wf = item.workflow_type ?? 'b2b';
    const group = groups.get(wf) ?? { wf, bars: [], lost: [] };
    (item.outcome === 'lost' ? group.lost : group.bars).push(item);
    groups.set(wf, group);
  }
  for (const group of groups.values()) group.bars.sort((a, b) => a.sort_order - b.sort_order);
  return [...groups.values()];
}

interface StageLayout {
  groups: StageGroup[];
  /** Верх каждого блока (заголовок процесса) и сетки, px. */
  blocks: { headerTop: number; gridTop: number; gridHeight: number }[];
  footerTop: number;
  height: number;
}

const STAGE_ROW = 26;
const STAGE_HEADER = 28;
const STAGE_GAP = 14;
const STAGE_AXIS = 26; // подписи шкалы под последним блоком
const STAGE_FOOTER = 30; // строка «Отказ»

/** Раскладка в пикселях: ECharts не понимает calc(), высота карточки — отсюда же. */
function stageLayout(series: DashboardStageItem[]): StageLayout {
  const groups = stageGroups(series);
  const several = groups.length > 1;
  const blocks: StageLayout['blocks'] = [];
  let y = 8;
  groups.forEach((group, index) => {
    const headerTop = y;
    if (several) y += STAGE_HEADER;
    const gridHeight = Math.max(group.bars.length, 1) * STAGE_ROW;
    blocks.push({ headerTop, gridTop: y, gridHeight });
    y += gridHeight + (index === groups.length - 1 ? STAGE_AXIS : STAGE_GAP);
  });
  const hasLost = groups.some((group) => group.lost.length > 0);
  return { groups, blocks, footerTop: y, height: Math.max(240, y + (hasLost ? STAGE_FOOTER : 8)) };
}

export function stageBarsHeight(series: DashboardStageItem[]): number {
  return stageLayout(series).height;
}

/**
 * Заявки по текущему этапу (виджет 2): горизонтальные бары от нуля в порядке этапов
 * процесса, общая шкала для всех процессов (B2B и B2C сравнимы). При «Все» — блок на
 * процесс с заголовком. «Отказ» — отдельной строкой под графиком, не частью
 * последовательности. Клик по этапу → канбан этого процесса с фильтром.
 */
export function stageBarsOption(series: DashboardStageItem[]): EChartsOption {
  const layout = stageLayout(series);
  const { groups } = layout;
  const several = groups.length > 1;
  const max = Math.max(1, ...series.filter((s) => s.outcome !== 'lost').map((s) => s.value));
  const graphic: Record<string, unknown>[] = [];
  const grids: Record<string, unknown>[] = [];
  const xAxes: Record<string, unknown>[] = [];
  const yAxes: Record<string, unknown>[] = [];
  const bars: Record<string, unknown>[] = [];
  groups.forEach((group, index) => {
    const block = layout.blocks[index]!;
    const last = index === groups.length - 1;
    if (several) {
      graphic.push({
        type: 'text',
        left: 8,
        top: block.headerTop + 6,
        silent: true,
        style: {
          text: `Процесс ${WORKFLOW_LABELS[group.wf] ?? group.wf.toUpperCase()}`,
          fill: SURFACE.foreground,
          font: '600 12px sans-serif',
        },
      });
    }
    grids.push({ left: 8, right: 56, top: block.gridTop, height: block.gridHeight, containLabel: true });
    xAxes.push({
      type: 'value',
      gridIndex: index,
      min: 0,
      max, // общая шкала: длины баров сравнимы между процессами
      minInterval: 1,
      axisLabel: { ...AXIS_LABEL, show: last },
      splitLine: SPLIT_LINE,
      name: last ? 'заявок' : undefined,
      nameTextStyle: AXIS_NAME,
    });
    yAxes.push({
      type: 'category',
      gridIndex: index,
      inverse: true,
      data: group.bars.map((item) => item.stage_label ?? item.label),
      axisLabel: { ...AXIS_LABEL, width: 150, overflow: 'truncate' },
    });
    bars.push({
      type: 'bar',
      name: WORKFLOW_LABELS[group.wf] ?? group.wf,
      xAxisIndex: index,
      yAxisIndex: index,
      barMaxWidth: 18,
      label: VALUE_LABEL,
      data: group.bars.map((item, i) => ({
        value: item.value,
        key: stageClickKey(item),
        stage: item.stage_label ?? item.label,
        amount: item.amount,
        itemStyle: { color: seriesColor(item, i), borderRadius: [0, 4, 4, 0] },
      })),
    });
  });
  if (groups.some((group) => group.lost.length > 0)) {
    const parts = groups.map((group) => {
      const lost = group.lost.reduce((sum, item) => sum + item.value, 0);
      return several ? `${WORKFLOW_LABELS[group.wf] ?? group.wf} — ${lost}` : `${lost}`;
    });
    const name = groups.find((group) => group.lost[0])?.lost[0]?.stage_label ?? 'Отказ';
    graphic.push({
      type: 'text',
      left: 8,
      top: layout.footerTop + 6,
      silent: true,
      style: {
        text: `${name}: ${parts.join(' · ')} — отдельно от последовательности этапов`,
        fill: STATUS_COLORS.danger,
        font: '500 12px sans-serif',
      },
    });
  }
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const p = params as {
          value: number;
          seriesName: string;
          data: { stage: string; amount?: string | null };
        };
        const amount =
          p.data.amount && Number(p.data.amount) > 0
            ? `<br/>Сумма: ${Number(p.data.amount).toLocaleString('ru-RU')} ₽`
            : '';
        const process = several ? `${escapeHtml(p.seriesName)} · ` : '';
        return `${process}${escapeHtml(p.data.stage)}: <b>${p.value}</b>${amount}`;
      },
    },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series: bars,
    graphic,
  } as EChartsOption;
}

// --- Динамика ----------------------------------------------------------------------------

const DYNAMICS_COLORS: Record<string, string> = {
  created: STATUS_COLORS.inProgress,
  closed: STATUS_COLORS.success,
  lost: STATUS_COLORS.danger,
};

function shortDate(iso: string): string {
  const [, month, day] = iso.split('-');
  return day && month ? `${day}.${month}` : iso;
}

/** Динамика по неделям (виджет 3): линии без сглаживания, точка на каждой неделе. */
export function dynamicsOption(series: ReportDynamicsSeries[]): EChartsOption {
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();
  const fallback = [STATUS_COLORS.inProgress, STATUS_COLORS.success, STATUS_COLORS.danger];
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      formatter: (params: unknown) => {
        const list = params as { axisValue: string; marker: string; seriesName: string; value: number }[];
        const first = list[0];
        if (!first) return '';
        const rows = list.map((p) => `${p.marker}${escapeHtml(p.seriesName)}: <b>${p.value}</b>`);
        return [`Неделя с ${shortDate(first.axisValue)}`, ...rows].join('<br/>');
      },
    },
    legend: { top: 0, textStyle: AXIS_LABEL },
    grid: { left: 48, right: 24, top: 40, bottom: 64 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { ...AXIS_LABEL, formatter: (value: string) => shortDate(value) },
      name: 'неделя',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: AXIS_NAME,
    },
    yAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: 'заявок',
      nameTextStyle: AXIS_NAME,
    },
    dataZoom: dates.length > 16 ? [{ type: 'slider', height: 16, bottom: 4 }] : [],
    series: series.map((s, index) => {
      const byDate = new Map(s.points.map((p) => [p.date, p.value]));
      const color = s.color || DYNAMICS_COLORS[s.key] || fallback[index % fallback.length]!;
      return {
        name: s.label,
        type: 'line' as const,
        // без сглаживания: сплайн рисовал бы значения между неделями, которых нет в данных
        smooth: false,
        showSymbol: true,
        symbol: 'circle',
        symbolSize: 6,
        data: dates.map((date) => byDate.get(date) ?? 0),
        itemStyle: { color },
        lineStyle: { color, width: 2 },
      };
    }),
  };
}

// --- Нагрузка по КАМам ------------------------------------------------------------------

interface StageMeta {
  code: string;
  labels: string[];
  color?: string | null;
  sort: number;
}

/** Этапы всех КАМов → серии по `code` (B2B и B2C с одним кодом — одна серия, сумма). */
export function workloadStages(rows: KamWorkloadItem[]): StageMeta[] {
  const meta = new Map<string, StageMeta>();
  for (const row of rows) {
    for (const item of row.by_status) {
      const code = item.code ?? item.key;
      const entry = meta.get(code) ?? { code, labels: [], color: item.color, sort: item.sort_order ?? 0 };
      if (!entry.labels.includes(item.label)) entry.labels.push(item.label);
      entry.sort = Math.min(entry.sort, item.sort_order ?? entry.sort);
      meta.set(code, entry);
    }
  }
  return [...meta.values()].sort((a, b) => a.sort - b.sort || a.code.localeCompare(b.code));
}

export function workloadCount(row: KamWorkloadItem, code: string): number {
  return row.by_status
    .filter((item) => (item.code ?? item.key) === code)
    .reduce((sum, item) => sum + (item.count ?? item.value ?? 0), 0);
}

/**
 * Нагрузка по КАМам (виджет 4): горизонтальный stacked bar по этапам в порядке
 * процесса; tooltip — этапы, вузы в работе (топ по числу заявок) и зависшие.
 */
export function kamWorkloadOption(rows: KamWorkloadItem[]): EChartsOption {
  const stages = workloadStages(rows);
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number; marker: string; seriesName: string; value: number }[];
        const first = list[0];
        if (!first) return '';
        const row = rows[first.dataIndex];
        if (!row) return '';
        const lines = [`<b>${escapeHtml(row.kam_name)}</b>`];
        for (const p of list) {
          if (p.value > 0) lines.push(`${p.marker}${escapeHtml(p.seriesName)}: <b>${p.value}</b>`);
        }
        lines.push(`Открытых заявок: <b>${row.active_requests}</b>`);
        lines.push(`Вузов в работе: <b>${row.universities}</b>`);
        lines.push(`Зависших: <b>${row.stuck}</b>`);
        if (row.top_universities.length > 0) {
          const names = row.top_universities.map((u) => `${escapeHtml(truncate(u.name, 36))} (${u.count})`);
          const more = row.universities > row.top_universities.length ? ` и ещё ${row.universities - row.top_universities.length}` : '';
          lines.push(`<span style="color:${SURFACE.mutedForeground}">${names.join(', ')}${more}</span>`);
        }
        return lines.join('<br/>');
      },
    },
    legend: { top: 0, type: 'scroll', textStyle: AXIS_LABEL },
    grid: { left: 8, right: 48, top: 36, bottom: 24, containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: 'заявок',
      nameTextStyle: AXIS_NAME,
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: rows.map((r) => r.kam_id),
      // подпись: КАМ · открытых заявок (зависших) — итог виден без наведения
      axisLabel: {
        ...AXIS_LABEL,
        formatter: (_value: string, index: number) => {
          const row = rows[index];
          if (!row) return '';
          const stuck = row.stuck > 0 ? `, зав. ${row.stuck}` : '';
          return `${truncate(row.kam_name, 22)} · ${row.active_requests}${stuck}`;
        },
      },
    },
    series: stages.map((stage, index) => ({
        name: stage.labels.join(' / '),
        type: 'bar' as const,
        stack: 'total',
        barMaxWidth: 22,
        // 2px белая обводка между stacked-сегментами (§2.6)
        itemStyle: { color: seriesColor(stage, index), borderWidth: 2, borderColor: SURFACE.card },
        data: rows.map((row) => ({ value: workloadCount(row, stage.code), key: row.kam_id })),
      })),
  };
}

// --- Востребованность программ -----------------------------------------------------------

export type ProgramsMode = 'requests' | 'students' | 'streams' | 'priority' | 'demand';

export const PROGRAM_MODES: { value: ProgramsMode; label: string; axis: string }[] = [
  { value: 'requests', label: 'Заявки', axis: 'заявок на обучение' },
  { value: 'students', label: 'Обучаются', axis: 'обучающихся' },
  { value: 'streams', label: 'Потоки', axis: 'параллельных потоков' },
  { value: 'priority', label: 'Приоритет', axis: 'заявок на обучение' },
  { value: 'demand', label: 'Индекс', axis: 'расчётный спрос, 0–100' },
];

export function programMetric(row: ProgramDemandItem, mode: ProgramsMode): number {
  switch (mode) {
    case 'students':
      return row.students_studying;
    case 'streams':
      return row.parallel_streams;
    case 'demand':
      return row.computed_demand ?? 0;
    default:
      return row.requests_count;
  }
}

/** Сортировка по выбранному показателю; «Приоритет» — по ручному рангу (меньше = выше). */
export function sortPrograms(rows: ProgramDemandItem[], mode: ProgramsMode): ProgramDemandItem[] {
  return [...rows].sort((a, b) =>
    mode === 'priority'
      ? a.priority_rank - b.priority_rank || a.name.localeCompare(b.name)
      : programMetric(b, mode) - programMetric(a, mode) ||
        b.requests_count - a.requests_count ||
        a.priority_rank - b.priority_rank,
  );
}

/** Востребованность программ (виджет 5): топ-10 по выбранному показателю ТЗ. */
export function programsOption(rows: ProgramDemandItem[], mode: ProgramsMode): EChartsOption {
  const top = sortPrograms(rows, mode).slice(0, 10);
  const axis = PROGRAM_MODES.find((m) => m.value === mode)?.axis ?? '';
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const list = params as { dataIndex: number }[];
        const first = list[0];
        if (!first) return '';
        const row = top[first.dataIndex];
        if (!row) return '';
        const lines = [
          `<b>${escapeHtml(row.name)}</b>`,
          `Заявки на обучение: <b>${row.requests_count}</b> (открыто ${row.open_requests})`,
          `Обучающиеся: <b>${row.students_studying}</b>`,
          `Параллельные потоки: <b>${row.parallel_streams}</b>`,
          `Ручной приоритет: ${row.priority_rank}`,
        ];
        if (row.computed_demand !== null) lines.push(`Расчётный спрос: <b>${row.computed_demand}</b>`);
        return lines.join('<br/>');
      },
    },
    grid: { left: 8, right: 48, top: 8, bottom: 28, containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: mode === 'demand' ? undefined : 1,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: axis,
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: AXIS_NAME,
    },
    yAxis: {
      type: 'category',
      inverse: true, // первая строка сверху
      data: top.map((r) => r.name),
      // длинные названия программ режутся по ширине, а не по числу символов
      axisLabel: { ...AXIS_LABEL, width: 170, overflow: 'truncate' },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 20,
        itemStyle: { color: STATUS_COLORS.inProgress, borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: 'right',
          color: SURFACE.mutedForeground,
          formatter: (p: unknown) => {
            const row = top[(p as { dataIndex: number }).dataIndex];
            if (!row) return '';
            const value = programMetric(row, mode);
            return mode === 'priority' ? `${value} · №${row.priority_rank}` : `${value}`;
          },
        },
        data: top.map((r) => ({ value: programMetric(r, mode), key: r.program_id })),
      },
    ],
  };
}

// --- Студенты ----------------------------------------------------------------------------

/** Студенты по статусам (виджет 6): бары от нуля в порядке пути, фиолетовая гамма. */
export function talentBarsOption(series: ReportSeriesItem[]): EChartsOption {
  return {
    ...chartAnimation(),
    tooltip: { trigger: 'item', formatter: (p: unknown) => {
      const item = p as { name: string; value: number };
      return `${escapeHtml(item.name)}: <b>${item.value}</b>`;
    } },
    grid: { left: 8, right: 48, top: 8, bottom: 28, containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      axisLabel: AXIS_LABEL,
      splitLine: SPLIT_LINE,
      name: 'студентов',
      nameLocation: 'middle',
      nameGap: 24,
      nameTextStyle: AXIS_NAME,
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: series.map((item) => item.label),
      axisLabel: AXIS_LABEL,
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 22,
        label: VALUE_LABEL,
        data: series.map((item, index) => ({
          value: item.value,
          key: item.key,
          itemStyle: {
            color: item.color || TALENT_PALETTE[index % TALENT_PALETTE.length]!,
            borderRadius: [0, 4, 4, 0],
          },
        })),
      },
    ],
  };
}
