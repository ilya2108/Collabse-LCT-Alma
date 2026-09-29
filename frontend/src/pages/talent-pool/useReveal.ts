import { useCallback, useEffect, useRef, useState } from 'react';
import { revealStudent } from '@/shared/api/endpoints/students';
import { notifyApiError } from '@/shared/api/feedback';
import type { StudentRevealResult } from '@/shared/api/types';
import { REVEAL_TTL_SECONDS } from './model';

/**
 * Раскрытие ПДн студентов (ux.md §12.2): POST /students/{id}/reveal пишет
 * аудит-событие на backend, открытые значения живут в памяти компонента
 * 60 секунд с обратным отсчётом, затем снова маскируются. Никакого
 * localStorage/кэша — ПДн не должны переживать экран.
 */

interface RevealedEntry {
  data: StudentRevealResult;
  expiresAt: number;
}

export interface RevealController {
  /** Открытые ПДн студента или null, если ещё/уже замаскировано. */
  revealed: (studentId: string) => StudentRevealResult | null;
  /** Секунды до повторного маскирования (для таймера-точки). */
  secondsLeft: (studentId: string) => number;
  reveal: (studentId: string) => void;
  isRevealing: (studentId: string) => boolean;
}

export function useReveal(): RevealController {
  const [entries, setEntries] = useState<Record<string, RevealedEntry>>({});
  const [pending, setPending] = useState<Record<string, boolean>>({});
  // тик раз в секунду, пока есть хоть одна раскрытая запись
  const [, setTick] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const hasEntries = Object.keys(entries).length > 0;
    if (!hasEntries) {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    if (timerRef.current === null) {
      timerRef.current = window.setInterval(() => {
        const now = Date.now();
        setEntries((prev) => {
          const alive = Object.entries(prev).filter(([, entry]) => entry.expiresAt > now);
          return alive.length === Object.keys(prev).length
            ? prev
            : Object.fromEntries(alive);
        });
        setTick((n) => n + 1);
      }, 1000);
    }
    return () => {
      if (timerRef.current !== null && Object.keys(entries).length === 0) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [entries]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    },
    [],
  );

  const reveal = useCallback((studentId: string) => {
    setPending((prev) => ({ ...prev, [studentId]: true }));
    revealStudent(studentId)
      .then((data) => {
        setEntries((prev) => ({
          ...prev,
          [studentId]: { data, expiresAt: Date.now() + REVEAL_TTL_SECONDS * 1000 },
        }));
      })
      .catch((error) => {
        notifyApiError(error, 'Не удалось получить данные');
      })
      .finally(() => {
        setPending((prev) => {
          const next = { ...prev };
          delete next[studentId];
          return next;
        });
      });
  }, []);

  return {
    revealed: useCallback(
      (studentId: string) => {
        const entry = entries[studentId];
        return entry && entry.expiresAt > Date.now() ? entry.data : null;
      },
      [entries],
    ),
    secondsLeft: useCallback(
      (studentId: string) => {
        const entry = entries[studentId];
        if (!entry) return 0;
        return Math.max(0, Math.ceil((entry.expiresAt - Date.now()) / 1000));
      },
      [entries],
    ),
    reveal,
    isRevealing: useCallback((studentId: string) => Boolean(pending[studentId]), [pending]),
  };
}
