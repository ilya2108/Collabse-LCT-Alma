/**
 * Единый источник фирменных цветов в виде hex-строк (redesign.md §1.1–§1.3).
 * CSS-слой берёт те же значения из токенов globals.css (OKLCH);
 * hex нужны там, где требуется строка: ECharts, @xyflow/react, canvas.
 * В компонентах hex запрещены — только tailwind-токены (§8.4).
 *
 * Ребрендинг под ДС «Ростелеком» gen2 (docs/design/brand-rt.md): все hex —
 * из фирменных рампов --atmr-* палитры РТК (base-info / status-01 / accent и др.).
 */

export type SemanticStatus = 'draft' | 'progress' | 'success' | 'warning' | 'danger' | 'talent';

export interface StatusTriple {
  /** Маркеры, полосы, графики. Текстом НЕ использовать (< 4.5:1 на белом). */
  core: string;
  /** Текст статусного цвета (везде, где статусный цвет — текст). AA ≥ 4.5. */
  deep: string;
  /** Фон бейджей/плашек. */
  tint: string;
}

/**
 * Статусные тройки core/deep/tint (brand-rt.md §2.3) — рампы ДС РТК:
 * core — ступень 600/base рампа, deep — 700/800 (AA ≥ 4.5 на tint, посчитано),
 * tint — ступень 25/50. Core совпадают с прежними STATUS_COLORS.
 */
export const STATUS_TRIPLES: Record<SemanticStatus, StatusTriple> = {
  draft: { core: '#585D69', deep: '#454B59', tint: '#F4F4F5' },     // base-neutral / 700 / 25, 7.95:1
  progress: { core: '#1F69FF', deep: '#164AB3', tint: '#E9F0FF' },  // base-info / 700 / 50, 6.90:1
  success: { core: '#09AD4D', deep: '#067A37', tint: '#E7FAEF' },   // success-600 / 800 / 50, 5.02:1
  warning: { core: '#D78D0E', deep: '#98640A', tint: '#FFF6E7' },   // warning-600 / 800 / 50, 4.71:1
  danger: { core: '#D92020', deep: '#B31B1B', tint: '#FFE9E9' },    // error-600 / 700 / 50, 5.85:1
  talent: { core: '#9233FF', deep: '#6500D9', tint: '#F1E6FF' },    // accent-400 / 600 / 50, 6.85:1
} as const;

/**
 * Легаси-словарь семантических цветов (ux.md §2.1) — прежние ключи и значения,
 * реэкспортируется из shared/config/theme.ts. Значения = core статусных троек.
 */
export const STATUS_COLORS = {
  /** Начальный этап / draft. */
  draft: STATUS_TRIPLES.draft.core,
  /** В работе (активные этапы). */
  inProgress: STATUS_TRIPLES.progress.core,
  /** Успех / завершено. */
  success: STATUS_TRIPLES.success.core,
  /** Ожидание / зависшая заявка. */
  stuck: STATUS_TRIPLES.warning.core,
  /** Отклонено / ошибка / красный сценарий. */
  danger: STATUS_TRIPLES.danger.core,
  /** Talent pool (фиолетовый акцент). */
  talentPool: STATUS_TRIPLES.talent.core,
} as const;

/**
 * Категориальная палитра графиков (§2.6): фиксированный порядок, не циклится,
 * 7-я серия складывается в «Прочее». Валидирована dataviz (CVD, контраст ≥3:1).
 */
export const CHART_PALETTE = [
  '#1F69FF', // синий РТК (base-info, = primary)
  '#D9430F', // оранжевый РТК (status-01-600, затемнён до контраста 4.41:1)
  '#148190', // бирюза (status-04-600)
  '#9233FF', // фиолетовый (accent-400)
  '#B81B5E', // малиновый (status-06-600)
  '#B1740B', // охра (warning-700)
] as const;

/** Рамп воронки talent pool (§2.6): фиолетовый рамп ДС accent-400/300/200/100. */
export const TALENT_FUNNEL_RAMP = ['#9233FF', '#A759FF', '#BB80FF', '#DDBFFF'] as const;

/** Пресет 8 цветов этапа в конструкторе workflow (§1.2): CHART_PALETTE + 2 из рампов ДС. */
export const STAGE_COLOR_PRESET = [
  '#1F69FF',
  '#D9430F',
  '#148190',
  '#9233FF',
  '#B81B5E',
  '#B1740B',
  '#09AD4D', // success-600
  '#585D69', // base-neutral
] as const;

/**
 * Тинт-фон колонки/бейджа произвольного цвета этапа (§1.2):
 * вычисляется в рантайме, чистым текстом цвет этапа не идёт никогда.
 */
export function stageTint(stageColor: string): string {
  return `color-mix(in oklab, ${stageColor} 10%, white)`;
}

/** Текст на тинт-фоне цвета этапа (§1.2): ≥4.5:1 для всех восьми пресетов. */
export function stageText(stageColor: string): string {
  return `color-mix(in oklab, ${stageColor} 78%, black)`;
}

/** Поверхности/текст (hex для ECharts, xyflow, canvas — §2.6). */
export const SURFACE = {
  background: '#F6F7FB', // фон LMS-референса
  card: '#FFFFFF',
  foreground: '#101828', // fg-default РТК (neutral-990)
  mutedForeground: '#585D69', // base-neutral
  muted: '#F4F4F5', // neutral-25
  border: '#E8E8EE', // neutral-50
  primary: '#1F69FF', // base-info, синий РТК
  primaryTint: '#F4F8FF', // info-25
  primaryTint2: '#E9F0FF', // info-50
  sidebar: '#151D2C', // neutral-950, графит РТК
  sidebarMuted: '#9DA1AC', // neutral-300
} as const;

/**
 * Фирменный оранжевый РТК (status-01): маркетинговые акценты и иллюстрации.
 * Текстом не использовать — 3.29:1 на белом (< AA).
 */
export const BRAND_ORANGE = '#FF4F12';
