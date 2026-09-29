import { useEffect, useState } from 'react';

/**
 * Замена Grid.useBreakpoint AntD (redesign.md §5.1) на matchMedia.
 * Пороговые значения = брейкпоинты Tailwind v4 (sm 640 / md 768 / lg 1024 / xl 1280) —
 * те же, что у responsive-колонок DataTable (§3.2).
 */
export interface Breakpoints {
  sm: boolean;
  md: boolean;
  lg: boolean;
  xl: boolean;
}

const QUERIES: Record<keyof Breakpoints, string> = {
  sm: '(min-width: 640px)',
  md: '(min-width: 768px)',
  lg: '(min-width: 1024px)',
  xl: '(min-width: 1280px)',
};

function read(): Breakpoints {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return { sm: true, md: true, lg: true, xl: true };
  }
  return {
    sm: window.matchMedia(QUERIES.sm).matches,
    md: window.matchMedia(QUERIES.md).matches,
    lg: window.matchMedia(QUERIES.lg).matches,
    xl: window.matchMedia(QUERIES.xl).matches,
  };
}

export function useBreakpoint(): Breakpoints {
  const [state, setState] = useState<Breakpoints>(read);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const lists = Object.values(QUERIES).map((q) => window.matchMedia(q));
    const update = (): void => setState(read());
    lists.forEach((l) => l.addEventListener('change', update));
    return () => lists.forEach((l) => l.removeEventListener('change', update));
  }, []);

  return state;
}
