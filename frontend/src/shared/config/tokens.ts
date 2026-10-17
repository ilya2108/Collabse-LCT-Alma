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

/**
 * Пресет 8 цветов этапа (§1.2): CHART_PALETTE + 2 из рампов ДС. Единая палитра
 * конструктора (pages/workflow/model.ts) и сидов схем (backend/app/modules/workflow/
 * seeds/*.json: «Новая» — серый, рабочие этапы — синий/бирюза/фиолетовый/охра/малиновый,
 * «Завершена» — зелёный, «Отказ» — оранжевый). Сверка сидов — frontend/tests и
 * backend/tests/test_demo_seed.py.
 */
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

/** Русские названия пресетов — aria-label свотчей конструктора. */
export const STAGE_COLOR_NAMES: Readonly<Record<string, string>> = {
  '#1F69FF': 'синий',
  '#D9430F': 'оранжевый',
  '#148190': 'бирюзовый',
  '#9233FF': 'фиолетовый',
  '#B81B5E': 'малиновый',
  '#B1740B': 'охра',
  '#09AD4D': 'зелёный',
  '#585D69': 'серый',
};

/*
 * — Цвет этапа на бейдже и колонке: контраст текста ≥ 4.5:1 (WCAG AA, §8.2) —
 * Цвет этапа приходит из БД: пресет, старые цвета сидов или любой hex из API.
 * Фон — 10 % цвета в белом, текст — цвет, затемнённый к чёрному в OKLab (как CSS
 * color-mix(in oklab, …)). Доля цвета в тексте подбирается: от 78 % вниз, пока текст не
 * даёт ≥ 4.5:1 и к своему тинту, и к белому. Оба значения считаются здесь и отдаются
 * hex — контраст проверяется ровно по тем цветам, которые нарисует браузер.
 */

type LinearRgb = readonly [number, number, number];

/** Порог AA для текста бейджа этапа. */
export const STAGE_TEXT_MIN_CONTRAST = 4.5;
const STAGE_TINT_SHARE = 0.1;
const STAGE_TEXT_START_SHARE = 0.78;
const STAGE_TEXT_SHARE_STEP = 0.02;
const WHITE: LinearRgb = [1, 1, 1];
const BLACK: LinearRgb = [0, 0, 0];

const srgbToLinear = (c: number): number => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const linearToSrgb = (c: number): number => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
const clamp01 = (c: number): number => Math.min(1, Math.max(0, c));

/** #RGB / #RRGGBB → линейный sRGB; null — не hex (var(), rgb() и пр.). */
function parseHex(color: string): LinearRgb | null {
  const value = color.trim();
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(value);
  const full = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value);
  const parts = full?.slice(1, 4) ?? short?.slice(1, 4).map((c) => c + c);
  if (!parts) return null;
  const [r, g, b] = parts.map((c) => srgbToLinear(parseInt(c, 16) / 255));
  return [r!, g!, b!];
}

/** Линейный sRGB → #RRGGBB (8 бит на канал — то, что увидит браузер). */
function toHex(rgb: LinearRgb): string {
  return `#${rgb
    .map((c) => Math.round(clamp01(linearToSrgb(clamp01(c))) * 255).toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase()}`;
}

function toOklab([r, g, b]: LinearRgb): [number, number, number] {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function fromOklab([L, A, B]: [number, number, number]): LinearRgb {
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3;
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3;
  const s = (L - 0.0894841775 * A - 1.291485548 * B) ** 3;
  return [
    clamp01(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    clamp01(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    clamp01(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

/** color-mix(in oklab, a share, b). */
function mixOklab(a: LinearRgb, b: LinearRgb, share: number): LinearRgb {
  const x = toOklab(a);
  const y = toOklab(b);
  return fromOklab([0, 1, 2].map((i) => x[i]! * share + y[i]! * (1 - share)) as [number, number, number]);
}

function luminance([r, g, b]: LinearRgb): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: LinearRgb, b: LinearRgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

/** Контраст двух hex-цветов по WCAG 2.x; NaN — если строка не hex. */
export function contrastRatio(foreground: string, background: string): number {
  const a = parseHex(foreground);
  const b = parseHex(background);
  return a && b ? ratio(a, b) : Number.NaN;
}

const tintCache = new Map<string, string>();
const textCache = new Map<string, string>();

/**
 * Тинт-фон колонки/бейджа цвета этапа (§1.2): 10 % цвета в белом, hex.
 * Не-hex цвет (var(), rgb()) — CSS color-mix как раньше.
 */
export function stageTint(stageColor: string): string {
  const cached = tintCache.get(stageColor);
  if (cached) return cached;
  const rgb = parseHex(stageColor);
  const tint = rgb
    ? toHex(mixOklab(rgb, WHITE, STAGE_TINT_SHARE))
    : `color-mix(in oklab, ${stageColor} 10%, white)`;
  tintCache.set(stageColor, tint);
  return tint;
}

/**
 * Текст на тинт-фоне цвета этапа (§1.2): самый «цветной» тёмный микс, дающий
 * ≥ 4.5:1 к тинту и к белому — для любого hex, а не только для пресетов.
 * Не-hex цвет — CSS color-mix с запасом (60 % к чёрному).
 */
export function stageText(stageColor: string): string {
  const cached = textCache.get(stageColor);
  if (cached) return cached;
  const rgb = parseHex(stageColor);
  let text = `color-mix(in oklab, ${stageColor} 60%, black)`;
  if (rgb) {
    const tint = parseHex(stageTint(stageColor))!;
    text = '#000000';
    for (let share = STAGE_TEXT_START_SHARE; share > 0; share -= STAGE_TEXT_SHARE_STEP) {
      const candidate = toHex(mixOklab(rgb, BLACK, share));
      const shown = parseHex(candidate)!;
      if (
        ratio(shown, tint) >= STAGE_TEXT_MIN_CONTRAST &&
        ratio(shown, WHITE) >= STAGE_TEXT_MIN_CONTRAST
      ) {
        text = candidate;
        break;
      }
    }
  }
  textCache.set(stageColor, text);
  return text;
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
