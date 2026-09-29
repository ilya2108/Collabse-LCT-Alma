import * as echarts from 'echarts';
import { prefersReducedMotion } from '@/shared/lib/motion';
import { CHART_PALETTE, SURFACE } from './tokens';

/**
 * Тема ECharts 'crm' (redesign.md §2.6, скилл dataviz): категориальная палитра
 * из tokens.ts, рецессивные оси/сетка, фирменный tooltip, анимации §2.3.
 * Регистрируется один раз; все ReactECharts получают theme={CRM_THEME}.
 */

export const CRM_THEME = 'crm';

const axisCommon = {
  axisLine: { lineStyle: { color: SURFACE.border } },
  axisTick: { lineStyle: { color: SURFACE.border } },
  axisLabel: { color: SURFACE.mutedForeground, fontSize: 12 },
  splitLine: { lineStyle: { color: SURFACE.muted } },
  splitArea: { show: false },
} as const;

let registered = false;

/** Идемпотентная регистрация темы. Вызывается из обёрток графиков (ChartCard). */
export function registerCrmTheme(): void {
  if (registered) return;
  registered = true;

  // §2.4: при prefers-reduced-motion графики не анимируются вовсе.
  const reduce = prefersReducedMotion();

  echarts.registerTheme(CRM_THEME, {
    color: [...CHART_PALETTE],
    backgroundColor: 'transparent',
    textStyle: {
      color: SURFACE.foreground,
      // = --font-sans globals.css: фирменный Rostelecom Basis с фолбэком Inter (brand-rt.md §3)
      fontFamily: "'Rostelecom Basis', 'Inter Variable', 'Segoe UI', system-ui, sans-serif",
    },
    // Вход графика (§2.3): 600ms cubicOut со ступенькой 40ms; смена фильтра — 240ms.
    animation: !reduce,
    animationDuration: 600,
    animationEasing: 'cubicOut',
    animationDelay: (i: number) => i * 40,
    animationDurationUpdate: 240,
    categoryAxis: axisCommon,
    valueAxis: axisCommon,
    timeAxis: axisCommon,
    logAxis: axisCommon,
    legend: {
      textStyle: { color: SURFACE.mutedForeground, fontSize: 12 },
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
    },
    tooltip: {
      backgroundColor: SURFACE.card,
      borderColor: SURFACE.border,
      borderWidth: 1,
      borderRadius: 8,
      textStyle: { color: SURFACE.foreground, fontSize: 12 },
      extraCssText: 'box-shadow: var(--shadow-overlay, 0 6px 20px rgba(16,35,61,0.12));',
      axisPointer: {
        lineStyle: { color: SURFACE.border },
        crossStyle: { color: SURFACE.border },
      },
    },
    line: {
      lineStyle: { width: 2 },
      symbolSize: 8,
      symbol: 'circle',
      showSymbol: false,
      smooth: false,
    },
    bar: {
      itemStyle: { borderRadius: [4, 4, 0, 0] },
    },
    pie: {
      itemStyle: { borderWidth: 2, borderColor: SURFACE.card },
      label: { color: SURFACE.foreground },
    },
    funnel: {
      itemStyle: { borderWidth: 2, borderColor: SURFACE.card },
    },
    graph: {
      color: [...CHART_PALETTE],
    },
  });
}

/**
 * Хелпер для option-уровневых анимационных настроек при перестроении option
 * (§2.4: проверять matchMedia при построении option).
 */
export function chartAnimation(): {
  animation: boolean;
  animationDuration: number;
  animationEasing: 'cubicOut';
  animationDurationUpdate: number;
} {
  const reduce = prefersReducedMotion();
  return {
    animation: !reduce,
    animationDuration: 600,
    animationEasing: 'cubicOut',
    animationDurationUpdate: 240,
  };
}
