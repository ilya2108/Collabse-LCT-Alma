import type { ReactNode } from 'react';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  SegmentedControl,
} from '@/shared/ui/data-table';
import type { IllustrationName } from '@/shared/illustrations';

/**
 * Совместимость WP5 (стадия Integrate): прежние Local*-адаптеры конструктора /
 * импорта / согласований теперь — тонкие обёртки над общими компонентами
 * PageHeader §3.3, SegmentedControl §3.10 и EmptyState/ErrorState §3.5
 * с иллюстрациями §4. Контракты вызовов не менялись.
 */

export function LocalPageHeader(props: {
  title: string;
  subtitle?: ReactNode;
  extra?: ReactNode;
  meta?: ReactNode;
}): ReactNode {
  return <PageHeader {...props} />;
}

export interface LocalSegmentedOption<V extends string> {
  value: V;
  label: string;
  count?: number;
}

export function LocalSegmented<V extends string>({
  options,
  value,
  onChange,
}: {
  options: Array<LocalSegmentedOption<V>>;
  value: V;
  onChange: (value: V) => void;
  'aria-label'?: string;
}): ReactNode {
  return <SegmentedControl<V> options={options} value={value} onChange={onChange} />;
}

export function LocalLoadingState({ rows = 6 }: { rows?: number }): ReactNode {
  return <LoadingState rows={rows} />;
}

export function LocalEmptyState({
  title,
  description,
  actions,
  compact,
  illustration = 'search-empty',
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  compact?: boolean;
  illustration?: IllustrationName;
}): ReactNode {
  return (
    <EmptyState
      illustration={illustration}
      title={title}
      description={description}
      actions={actions}
      compact={compact}
    />
  );
}

export function LocalErrorState(props: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}): ReactNode {
  return <ErrorState {...props} />;
}
