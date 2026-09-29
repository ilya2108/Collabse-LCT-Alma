/**
 * Визуальный язык (docs/design/redesign.md §1): токены живут в
 * src/styles/globals.css (OKLCH) и shared/config/tokens.ts (hex для
 * ECharts/xyflow). AntD-тема удалена на стадии Integrate; файл остаётся
 * ради обратной совместимости реэкспорта STATUS_COLORS.
 */

/** Ширина сайдбара (redesign.md §1.4): развёрнут 248, свёрнут 64. */
export const SIDER_WIDTH = 248;
export const SIDER_COLLAPSED_WIDTH = 64;

/**
 * Семантические цвета статусов (ux.md §2.1) — единые по всему приложению:
 * канбан, теги, таймлайн, дашборд. Единый источник значений —
 * shared/config/tokens.ts (redesign.md §1.1), здесь только реэкспорт.
 */
export { STATUS_COLORS } from './tokens';
