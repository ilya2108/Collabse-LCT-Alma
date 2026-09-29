import type { LucideIcon } from 'lucide-react';
import { TrendingDown, TrendingUp } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { useCountUp } from '@/shared/lib/useCountUp';
import { Skeleton } from '@/shared/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * StatCard — KPI-карточка (redesign.md §3.4, замена Statistic): иконка в
 * тонированном квадрате 40px, число 28/36 с count-up и tabular-nums, дельта
 * к прошлому периоду. Кликабельная карточка — кнопка drill-down с hover-лифтом.
 * Контракт пропсов — API-заморозка (§9).
 */

export interface StatCardProps {
  title: string;
  icon: LucideIcon;
  tone: 'primary' | 'success' | 'warning' | 'danger' | 'talent' | 'draft';
  value: number;
  /** Например '%'. */
  suffix?: string;
  /** Стрелка-изменение к прошлому периоду той же длины. */
  delta?: number;
  /** true — рост показателя это плохо (зависшие). */
  invertedDelta?: boolean;
  /** Тултип-пояснение метрики. */
  hint?: string;
  /** Drill-down; карточка становится кнопкой. */
  onClick?: () => void;
  /** Скелетон той же геометрии. */
  loading?: boolean;
}

/** Литеральные классы токенов §1.2 (текст — всегда deep, фон — tint). */
const TONE_CLASSES: Record<StatCardProps['tone'], string> = {
  primary: 'bg-primary-tint text-primary',
  success: 'bg-status-success-tint text-status-success-deep',
  warning: 'bg-status-warning-tint text-status-warning-deep',
  danger: 'bg-status-danger-tint text-status-danger-deep',
  talent: 'bg-status-talent-tint text-status-talent-deep',
  draft: 'bg-status-draft-tint text-status-draft-deep',
};

function Delta({ delta, inverted }: { delta: number; inverted: boolean }): ReactNode {
  const positive = delta > 0;
  const good = inverted ? !positive : positive;
  const Icon = positive ? TrendingUp : TrendingDown;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-xs font-medium tabular',
            good ? 'text-status-success-deep' : 'text-status-danger-deep',
          )}
        >
          <Icon className="size-3" aria-hidden="true" />
          {Math.abs(delta)}
        </span>
      </TooltipTrigger>
      <TooltipContent>Изменение к прошлому периоду той же длины</TooltipContent>
    </Tooltip>
  );
}

export function StatCard({
  title,
  icon: Icon,
  tone,
  value,
  suffix,
  delta,
  invertedDelta = false,
  hint,
  onClick,
  loading = false,
}: StatCardProps): ReactNode {
  const animated = useCountUp(value);

  if (loading) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-border bg-card p-5 shadow-card">
        <Skeleton className="size-10 shrink-0 rounded-md" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Skeleton className="h-3 w-2/3 rounded-sm" />
          <Skeleton className="h-7 w-1/3 rounded-sm" />
        </div>
      </div>
    );
  }

  const body = (
    <>
      <span
        className={cn(
          'flex size-10 shrink-0 items-center justify-center rounded-md',
          TONE_CLASSES[tone],
        )}
      >
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-xs font-medium text-muted-foreground">{title}</span>
        <span className="flex items-baseline gap-2">
          <span className="tabular text-[28px] font-semibold leading-9 text-foreground">
            {animated}
            {suffix}
          </span>
          {delta !== undefined && delta !== 0 ? (
            <Delta delta={delta} inverted={invertedDelta} />
          ) : null}
        </span>
      </span>
    </>
  );

  const card = onClick ? (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${title}: ${value}${suffix ?? ''}. Открыть детализацию`}
      className={cn(
        'flex w-full cursor-pointer items-center gap-3 rounded-lg border border-border bg-card p-5 text-left shadow-card',
        'transition-[box-shadow,transform,border-color] duration-150 hover:-translate-y-0.5 hover:shadow-overlay',
      )}
    >
      {body}
    </button>
  ) : (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card p-5 shadow-card">
      {body}
    </div>
  );

  if (!hint) return card;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{card}</TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}
