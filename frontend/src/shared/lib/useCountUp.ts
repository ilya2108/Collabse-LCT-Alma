import { useEffect, useRef, useState } from 'react';

/**
 * Count-up для цифр KPI (ux.md §2.3): анимация только при первом появлении
 * значения; `prefers-reduced-motion` отключает её полностью.
 */
export function useCountUp(target: number, durationMs = 800): number {
  const [value, setValue] = useState(0);
  const animatedRef = useRef(false);

  useEffect(() => {
    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || animatedRef.current) {
      animatedRef.current = true;
      setValue(target);
      return;
    }
    animatedRef.current = true;
    const start = performance.now();
    let frame = 0;
    const tick = (now: number): void => {
      const progress = Math.min(1, (now - start) / durationMs);
      // ease-out cubic — быстро в начале, мягко в конце
      const eased = 1 - (1 - progress) ** 3;
      setValue(Math.round(target * eased));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs]);

  return value;
}
