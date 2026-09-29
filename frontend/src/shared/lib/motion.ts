import { useReducedMotion } from 'motion/react';
import type { Transition } from 'motion/react';

/**
 * Моушн-токены (redesign.md §2.1) — единственный источник значений.
 * Инлайновые duration/stiffness в компонентах запрещены — только импорт отсюда.
 * Импорт анимаций строго `from 'motion/react'` (framer-motion запрещён, Р3).
 */
export const motionTokens = {
  duration: { instant: 0.08, fast: 0.18, normal: 0.35, slow: 0.6 },
  easing: {
    smooth: [0.22, 1, 0.36, 1] as const,   // вход элементов
    sharp:  [0.4, 0, 0.2, 1] as const,     // выход/сворачивание
  },
  distance: { xs: 4, sm: 8, md: 16, lg: 24 },
  scale: { subtle: 0.98, press: 0.97, pop: 1.02 },
} as const;

export const springs = {
  snappy:  { type: 'spring', stiffness: 300, damping: 30 },  // дефолт UI: чипы, кнопки, сегменты
  gentle:  { type: 'spring', stiffness: 120, damping: 14 },  // карточки, панели, модалки
  bouncy:  { type: 'spring', stiffness: 400, damping: 10 },  // онбординг, иллюстрационные моменты
  instant: { type: 'spring', stiffness: 600, damping: 35 },  // тултипы, поповеры, дропдауны
  release: { type: 'spring', stiffness: 200, damping: 20, restDelta: 0.001 }, // отпускание drag
} as const satisfies Record<string, Transition>;

/**
 * Безопасные variants входа (§2.4): при prefers-reduced-motion — только
 * fade ≤ 0.2s без смещения. Для ручных initial/animate в компонентах.
 */
export function useSafeMotion(distance: number = motionTokens.distance.sm): {
  initial: { opacity: number; y: number };
  animate: { opacity: number; y: number };
  transition: Transition;
} {
  const reduced = useReducedMotion() ?? false;
  if (reduced) {
    return {
      initial: { opacity: 0, y: 0 },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0.2 },
    };
  }
  return {
    initial: { opacity: 0, y: distance },
    animate: { opacity: 1, y: 0 },
    transition: { duration: motionTokens.duration.normal, ease: motionTokens.easing.smooth },
  };
}

/**
 * Императивная проверка reduce вне React (ECharts option, count-up и т.п., §2.4).
 */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * Стандартные variants стаггера списков/карточек (§2.3): контейнер +
 * элемент. Максимум 12 анимируемых элементов, шаг 0.06s.
 */
export const staggerContainer = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } },
} as const;

export const staggerItem = {
  hidden: { opacity: 0, y: motionTokens.distance.md },
  visible: { opacity: 1, y: 0, transition: springs.gentle },
} as const;
