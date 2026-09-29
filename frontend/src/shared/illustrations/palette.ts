/**
 * Палитра иллюстраций (redesign.md §4.1): дуотон бренда через CSS-токены
 * с hex-фолбэком. Контур — графит сайдбара, плоскости — два тинта,
 * акцент — один на сюжет (бренд / talent / danger).
 * Ребрендинг РТК (brand-rt.md §5): hex-фолбэки = рампы ДС, дуотон
 * пересаживается автоматически через var().
 */

export const ILL = {
  /** Контурные линии, stroke 1.5, round joins/caps. */
  line: 'var(--sidebar, #151D2C)',
  /** Светлая плоскость. */
  fill1: 'var(--primary-tint, #F4F8FF)',
  /** Плоскость потемнее. */
  fill2: 'var(--primary-tint-2, #E9F0FF)',
  /** Акцент бренда (≤ 20% площади). */
  accent: 'var(--primary, #1F69FF)',
  /** Фирменный оранжевый РТК — «огонёк» в 1–2 сюжетах онбординга (≤ 10% площади). */
  orange: 'var(--brand-orange, #FF4F12)',
  /** Акцент talent pool-сюжетов. */
  talent: 'var(--status-talent, #9233FF)',
  /** Акцент danger-сюжетов. */
  danger: 'var(--status-danger, #D92020)',
  /** Подложка-«тень» эллипсом. */
  shadow: 'var(--muted, #F4F4F5)',
  /** Белый (внутренности акцентных кружков). */
  white: 'var(--card, #FFFFFF)',
} as const;

/** Общие атрибуты корневого svg сюжета (§4.1: viewBox 240×160, нет CLS). */
export const SVG_PROPS = {
  viewBox: '0 0 240 160',
  fill: 'none',
  xmlns: 'http://www.w3.org/2000/svg',
  'aria-hidden': true,
} as const;

/** Общие атрибуты контурных путей. */
export const STROKE_PROPS = {
  stroke: ILL.line,
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const;
