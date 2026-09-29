import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';
import { errorMessage, isApiError } from '@/shared/api/errors';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import { Skeleton } from '@/shared/ui/skeleton';
import { Illustration } from '@/shared/illustrations';
import type { IllustrationName } from './types';

/**
 * Состояния «пусто / пусто-по-фильтрам / ошибка / загрузка» нового кита
 * (redesign.md §3.5, ux.md §4.1: у каждого состояния есть следующий шаг).
 * Иллюстрации — общий реестр shared/illustrations (§4, 12 сюжетов).
 */

export { Illustration };

// --- EmptyState (§3.5) -------------------------------------------------------

export interface EmptyStateProps {
  illustration?: IllustrationName;
  title: string;
  description?: ReactNode;
  /** 1 primary + до 1 secondary CTA. */
  actions?: ReactNode;
  /** Внутри виджетов/ячеек: SVG 96px, без description. */
  compact?: boolean;
}

export function EmptyState({
  illustration = 'registry-empty',
  title,
  description,
  actions,
  compact = false,
}: EmptyStateProps): ReactNode {
  const reduced = useReducedMotion() ?? false;
  return (
    <div className={cn('flex flex-col items-center text-center', compact ? 'gap-2 py-4' : 'gap-3 py-10')}>
      <motion.div
        initial={reduced ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.96 }}
        animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
        transition={reduced ? { duration: 0.2 } : springs.bouncy}
      >
        <Illustration name={illustration} height={compact ? 96 : 160} />
      </motion.div>
      <p className="text-base font-semibold text-balance">{title}</p>
      {description && !compact ? (
        <p className="max-w-[420px] text-sm text-muted-foreground">{description}</p>
      ) : null}
      {actions ? <div className="mt-1 flex flex-wrap items-center justify-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Пусто из-за фильтров — сюжет search-empty + сброс (ux.md §4.1). */
export function FilteredEmptyState({ onReset }: { onReset: () => void }): ReactNode {
  return (
    <EmptyState
      illustration="search-empty"
      title="Ничего не найдено"
      description="По текущим фильтрам и поиску записей нет"
      actions={
        <Button variant="outline" onClick={onReset}>
          Сбросить фильтры
        </Button>
      }
    />
  );
}

export interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}

/** Ошибка загрузки: сюжет error, текст без стектрейса, requestId мелко. */
export function ErrorState({ error, onRetry, title = 'Не удалось загрузить данные' }: ErrorStateProps): ReactNode {
  const traceId = isApiError(error) ? error.traceId : null;
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <Illustration name="error-broken" height={140} />
      <p className="text-base font-semibold text-balance">{title}</p>
      <p className="max-w-[420px] text-sm text-muted-foreground">{errorMessage(error)}</p>
      {traceId ? <p className="text-xs text-muted-foreground">Код обращения: {traceId}</p> : null}
      {onRetry ? (
        <Button className="mt-1" onClick={onRetry}>
          Повторить
        </Button>
      ) : null}
    </div>
  );
}

interface LoadingStateProps {
  rows?: number;
  /** Обернуть в карточку (для страниц); false — голый скелетон. */
  card?: boolean;
}

/** Первичная загрузка: скелетон-строки с shimmer (§2.3), не спиннер. */
export function LoadingState({ rows = 6, card = true }: LoadingStateProps): ReactNode {
  const body = (
    <div className="flex flex-col gap-3" aria-hidden="true">
      <Skeleton className="h-4 w-1/3" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-4" style={{ width: `${100 - ((i * 13) % 30)}%` }} />
      ))}
    </div>
  );
  return card ? <div className="rounded-lg border bg-card p-5 shadow-card">{body}</div> : body;
}
