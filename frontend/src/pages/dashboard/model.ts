import type { EChartsOption } from 'echarts';
import type {
  KamWorkloadRow,
  ProgramDemandRow,
  ReportDynamicsSeries,
  ReportSeriesItem,
} from '@/shared/api/types';
import { chartAnimation } from '@/shared/config/echartsTheme';
import { CHART_PALETTE, STATUS_COLORS, SURFACE, TALENT_FUNNEL_RAMP } from '@/shared/config/tokens';

/**
 * Опции ECharts для виджетов дашборда (ux.md §6.1). Цвета этапов приходят
 * из конфига workflow внутри отчёта (§7.2) — дашборд и канбан раскрашены из
 * одного источника; фолбэк — категориальная палитра темы 'crm'
 * (redesign.md §2.6, hex из tokens.ts — где нужны строки).
 */

const FALLBACK_PALETTE = [...CHART_PALETTE];

/** Фиолетовый рамп воронки студентов (§2.6): светлеет к концу. */
const TALENT_PALETTE = [...TALENT_FUNNEL_RAMP];

const AXIS_LABEL = { color: SURFACE.mutedForeground, fontSize: 12 };
const SPLIT_LINE = { lineStyle: { color: SURFACE.border } };

function seriesColor(item: ReportSeriesItem, index: number, palette = FALLBACK_PALETTE): string {
  return item.color || palette[index % palette.length]!;
}

/*
 * — Подписи внутри воронок: контраст ≥ 4.5 (§8.2) —
 * ECharts рисует в canvas, где CSS-строка color-mix() недоступна, поэтому
 * §1.2-формула «микс цвета к чёрному в OKLab» посчитана локально в JS
 * (адаптер к stageText() из tokens.ts). Белая подпись остаётся только на
 * сегментах, где она даёт ≥ 4.5:1; на светлых (например, хвост фиолетового
 * рампа talent pool) берётся тёмный микс цвета самого сегмента — первая доля,
 * проходящая порог. Если белый < 4.5, то почти-чёрный ≥ 4.5 — подбор конечен.
 */

const srgbToLinear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const linearToSrgb = (c: number): number => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
const clamp01 = (c: number): number => Math.min(1, Math.max(0, c));

/** #RGB/#RRGGBB → линейный RGB [0..1]; null для нераспознанной строки. */
function hexToLinearRgb(hex: string): [number, number, number] | null {
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(hex);
  const full = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  const parts = full?.slice(1, 4) ?? short?.slice(1, 4).map((c) => c + c);
  if (!parts) return null;
  const [r, g, b] = parts.map((c) => srgbToLinear(parseInt(c, 16) / 255));
  return [r!, g!, b!];
}

function relativeLuminance([r, g, b]: [number, number, number]): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

/** color-mix(in oklab, C share, black): в OKLab это масштаб L/a/b на share. */
function mixBlackOklab([r, g, b]: [number, number, number], share: number): [number, number, number] {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = (0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s) * share;
  const A = (1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s) * share;
  const B = (0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s) * share;
  const l2 = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m2 = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s2 = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [
    clamp01(4.0767416621 * l2 - 3.3077115913 * m2 + 0.2309699292 * s2),
    clamp01(-1.2684380046 * l2 + 2.6097574011 * m2 - 0.3413193965 * s2),
    clamp01(-0.0041960863 * l2 - 0.7034186147 * m2 + 1.707614701 * s2),
  ];
}

function linearRgbToHex(rgb: [number, number, number]): string {
  return `#${rgb
    .map((c) =>
      Math.round(clamp01(linearToSrgb(c)) * 255)
        .toString(16)
        .padStart(2, '0'),
    )
    .join('')}`;
}

const WHITE_LINEAR: [number, number, number] = [1, 1, 1];
const AA_TEXT = 4.5; // §8.2: порог для текста 12px
const labelColorCache = new Map<string, string>();

/** Цвет подписи внутри сегмента воронки: гарантированные ≥ 4.5:1 к сегменту. */
function funnelLabelColor(segmentColor: string): string {
  const cached = labelColorCache.get(segmentColor);
  if (cached) return cached;
  const segment = hexToLinearRgb(segmentColor);
  let label: string = SURFACE.card; // фолбэк для нераспознанного цвета — прежнее поведение
  if (segment && contrastRatio(WHITE_LINEAR, segment) < AA_TEXT) {
    let share = 0.78; // стартовая доля из §1.2, дальше темнее до порога
    let mixed = mixBlackOklab(segment, share);
    while (contrastRatio(mixed, segment) < AA_TEXT && share > 0.08) {
      share -= 0.03;
      mixed = mixBlackOklab(segment, share);
    }
    label = linearRgbToHex(mixed);
  }
  labelColorCache.set(segmentColor, label);
  return label;
}

/** Воронка по этапам workflow (виджет 2): клик по этапу → канбан с фильтром. */
export function funnelOption(series: ReportSeriesItem[]): EChartsOption {
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number; data: { amount?: string | null } };
        const amount = p.data.amount
          ? `<br/>Сумма: ${Number(p.data.amount).toLocaleString('ru-RU')} ₽`
          : '';
        return `${p.name}: <b>${p.value}</b>${amount}`;
      },
    },
    series: [
      {
        type: 'funnel',
        left: 16,
        right: 16,
        top: 8,
        bottom: 8,
        sort: 'none',
        gap: 4,
        minSize: '12%',
        label: { show: true, position: 'inside', formatter: '{b}: {c}' },
        emphasis: { label: { fontSize: 14 } },
        data: series.map((item, index) => {
          const color = seriesColor(item, index);
          return {
            name: item.label,
            value: item.value,
            key: item.key,
            amount: item.amount,
            // Подпись контрастна цвету сегмента (§8.2), не жёсткий белый
            label: { color: funnelLabelColor(color) },
            // 2px белая обводка между сегментами воронки (§2.6)
            itemStyle: { color, borderWidth: 2, borderColor: SURFACE.card },
          };
        }),
      },
    ],
  };
}

/** Динамика создано/завершено по неделям (виджет 3): area + dataZoom. */
export function dynamicsOption(series: ReportDynamicsSeries[]): EChartsOption {
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();
  const colors = [STATUS_COLORS.inProgress, STATUS_COLORS.success, STATUS_COLORS.stuck];
  return {
    ...chartAnimation(),
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { top: 0, textStyle: AXIS_LABEL },
    grid: { left: 48, right: 16, top: 32, bottom: 56 },
    xAxis: { type: 'category', data: dates, axisLabel: AXIS_LABEL },
    yAxis: { type: 'value', minInterval: 1, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    dataZoom: [{ type: 'slider', height: 20, bottom: 8 }],
    series: series.map((s, index) => {
      const byDate = new Map(s.points.map((p) => [p.date, p.value]));
      const color = s.color || colors[index % colors.length]!;
      return {
        name: s.label,
        type: 'line' as const,
        smooth: true,
        symbolSize: 6,
        data: dates.map((date) => byDate.get(date) ?? 0),
        itemStyle: { color },
        lineStyle: { color },
        areaStyle: { color, opacity: 0.12 },
      };
    }),
  };
}

/** Нагрузка по КАМам (виджет 4): горизонтальный stacked bar по этапам. */
export function kamWorkloadOption(rows: KamWorkloadRow[]): EChartsOption {
  // единый набор этапов по всем КАМам — в порядке первого появления
  const statusKeys: string[] = [];
  const statusMeta = new Map<string, ReportSeriesItem>();
  for (const row of rows) {
    for (const item of row.by_status) {
      if (!statusMeta.has(item.key)) {
        statusKeys.push(item.key);
        statusMeta.set(item.key, item);
      }
    }
  }
  const kamNames = rows.map((r) => r.kam_name);
  return {
    ...chartAnimation(),
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, type: 'scroll', textStyle: AXIS_LABEL },
    grid: { left: 8, right: 24, top: 32, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    yAxis: { type: 'category', data: kamNames, axisLabel: AXIS_LABEL },
    series: statusKeys.map((key, index) => {
      const meta = statusMeta.get(key)!;
      return {
        name: meta.label,
        type: 'bar' as const,
        stack: 'total',
        barMaxWidth: 24,
        // 2px белая обводка между stacked-сегментами (§2.6)
        itemStyle: { color: seriesColor(meta, index), borderWidth: 2, borderColor: SURFACE.card },
        data: rows.map((row) => {
          const found = row.by_status.find((s) => s.key === key);
          return { value: found?.value ?? 0, key: row.kam_id };
        }),
      };
    }),
  };
}

export type ProgramsMode = 'requests' | 'priority';

/** Топ программ (виджет 5): тумблер «по заявкам / по приоритету» (ux.md §9.4). */
export function programsOption(rows: ProgramDemandRow[], mode: ProgramsMode): EChartsOption {
  const sorted = [...rows].sort((a, b) =>
    mode === 'requests' ? b.requests_count - a.requests_count : a.priority_rank - b.priority_rank,
  );
  const top = sorted.slice(0, 10).reverse(); // reverse: первая строка сверху
  return {
    ...chartAnimation(),
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: unknown) => {
        const list = params as { name: string; value: number; dataIndex: number }[];
        const first = list[0];
        if (!first) return '';
        const row = top[first.dataIndex];
        return `${first.name}<br/>Заявок: <b>${first.value}</b><br/>Приоритет: ${row?.priority_rank ?? '—'}`;
      },
    },
    grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
    yAxis: {
      type: 'category',
      data: top.map((r) => (r.name.length > 28 ? `${r.name.slice(0, 27)}…` : r.name)),
      axisLabel: AXIS_LABEL,
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 20,
        itemStyle: { color: STATUS_COLORS.inProgress, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: SURFACE.mutedForeground },
        data: top.map((r) => ({ value: r.requests_count, key: r.program_id })),
      },
    ],
  };
}

/** Воронка студентов talent pool (виджет 6): фиолетовая гамма. */
export function talentFunnelOption(series: ReportSeriesItem[]): EChartsOption {
  return {
    ...chartAnimation(),
    tooltip: { trigger: 'item', formatter: '{b}: <b>{c}</b>' },
    series: [
      {
        type: 'funnel',
        left: 16,
        right: 16,
        top: 8,
        bottom: 8,
        sort: 'none',
        gap: 4,
        minSize: '16%',
        label: { show: true, position: 'inside', formatter: '{b}: {c}' },
        data: series.map((item, index) => {
          const color = item.color || TALENT_PALETTE[index % TALENT_PALETTE.length]!;
          return {
            name: item.label,
            value: item.value,
            key: item.key,
            // Хвост рампа светлеет (§2.6) — подпись темнеет вместе с ним (§8.2)
            label: { color: funnelLabelColor(color) },
            itemStyle: { color, borderWidth: 2, borderColor: SURFACE.card },
          };
        }),
      },
    ],
  };
}

/**
 * Композитный PNG всего дашборда (ux.md §6.2): собирается на клиенте из
 * getDataURL каждого графика + подпись с периодом и фильтрами. Без запросов
 * на сервер — работает и офлайн.
 */
export async function composeDashboardPng(
  charts: { title: string; dataUrl: string }[],
  caption: string,
): Promise<void> {
  const WIDTH = 1200;
  const PADDING = 24;
  const TITLE_H = 34;
  const HEADER_H = 72;

  const images = await Promise.all(
    charts.map(
      (chart) =>
        new Promise<{ title: string; img: HTMLImageElement }>((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve({ title: chart.title, img });
          img.onerror = () => reject(new Error(`Не удалось подготовить график «${chart.title}»`));
          img.src = chart.dataUrl;
        }),
    ),
  );

  const contentWidth = WIDTH - PADDING * 2;
  const blocks = images.map(({ title, img }) => {
    const scale = contentWidth / img.width;
    return { title, img, height: Math.round(img.height * scale) };
  });
  const totalHeight =
    HEADER_H + blocks.reduce((sum, b) => sum + TITLE_H + b.height + PADDING, 0) + PADDING;

  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = totalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Браузер не поддерживает canvas');

  ctx.fillStyle = SURFACE.card;
  ctx.fillRect(0, 0, WIDTH, totalHeight);
  ctx.fillStyle = SURFACE.foreground;
  ctx.font = '600 22px Inter, "Segoe UI", sans-serif';
  ctx.fillText('Альма — аналитика', PADDING, 36);
  ctx.fillStyle = SURFACE.mutedForeground;
  ctx.font = '400 14px Inter, "Segoe UI", sans-serif';
  ctx.fillText(caption, PADDING, 58);

  let y = HEADER_H;
  for (const block of blocks) {
    ctx.fillStyle = SURFACE.foreground;
    ctx.font = '600 16px Inter, "Segoe UI", sans-serif';
    ctx.fillText(block.title, PADDING, y + 20);
    y += TITLE_H;
    ctx.drawImage(block.img, PADDING, y, contentWidth, block.height);
    y += block.height + PADDING;
  }

  const anchor = document.createElement('a');
  anchor.href = canvas.toDataURL('image/png');
  anchor.download = 'crm-dashboard.png';
  anchor.click();
}
