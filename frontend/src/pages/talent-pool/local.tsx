import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { isApiError } from '@/shared/api/errors';
import type { IllustrationName } from '@/shared/illustrations';
import {
  EmptyState as SharedEmptyState,
  ErrorState as SharedErrorState,
  LoadingState,
  SegmentedControl as SharedSegmentedControl,
} from '@/shared/ui/data-table';
import { Button } from '@/shared/ui/button';

/**
 * Совместимость WP6 (стадия Integrate): прежние локальные адаптеры
 * замороженных контрактов §3.3/§3.5/§3.6/§3.7/§3.10 заменены реэкспортами
 * и тонкими обёртками над общими компонентами shared/ui.
 */

export { PageHeader, type PageHeaderProps } from '@/shared/ui/data-table/page-header';
export { StatusBadge, type StatusBadgeProps } from '@/shared/ui/data-table/status-badge';
export {
  Timeline,
  type TimelineItem,
  type TimelineProps,
} from '@/shared/ui/timeline';

// --- EmptyState / ErrorState (§3.5) ------------------------------------------

export interface EmptyStateProps {
  illustration: IllustrationName;
  title: string;
  description?: string;
  actions?: ReactNode;
  compact?: boolean;
}

export function EmptyState(props: EmptyStateProps): ReactNode {
  return <SharedEmptyState {...props} />;
}

export interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
}

export function ErrorState({
  error,
  onRetry,
  title = 'Не удалось загрузить данные',
  compact,
}: ErrorStateProps): ReactNode {
  if (!compact) return <SharedErrorState error={error} onRetry={onRetry} title={title} />;
  const traceId = isApiError(error) ? error.traceId : null;
  const message = isApiError(error) ? error.message : null;
  return (
    <SharedEmptyState
      illustration="error-broken"
      title={title}
      description={message ?? undefined}
      compact
      actions={
        <div className="flex flex-col items-center gap-2">
          {onRetry ? (
            <Button variant="outline" onClick={onRetry}>
              Повторить
            </Button>
          ) : null}
          {traceId ? (
            <span className="text-xs text-muted-foreground">Код обращения: {traceId}</span>
          ) : null}
        </div>
      }
    />
  );
}

/** Скелетон-блок загрузки (§2.3: shimmer, повторяет каркас списка). */
export function LoadingBlock({ rows = 6 }: { rows?: number }): ReactNode {
  return <LoadingState rows={rows} />;
}

// --- SegmentedControl (§3.10) --------------------------------------------------

export interface SegmentedOption {
  value: string;
  label: string;
  icon?: LucideIcon;
  count?: number;
}

export interface SegmentedControlProps {
  options: SegmentedOption[];
  value: string;
  onChange: (value: string) => void;
  size?: 'sm' | 'md';
  /** aria-label группы. */
  label?: string;
  /** Прежний layoutId подложки: общий компонент генерирует его сам. */
  layoutId?: string;
}

export function SegmentedControl({
  options,
  value,
  onChange,
  size,
}: SegmentedControlProps): ReactNode {
  return <SharedSegmentedControl options={options} value={value} onChange={onChange} size={size} />;
}
